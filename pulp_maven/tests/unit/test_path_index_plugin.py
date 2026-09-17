"""Verify public content-extension hooks in an isolated Django process."""

import os
import subprocess
import sys
import textwrap


def test_public_plugin_content_hooks():
    script = textwrap.dedent(
        """\
        import os
        from tempfile import NamedTemporaryFile
        from unittest.mock import patch
        from cryptography.fernet import Fernet

        with NamedTemporaryFile() as key, NamedTemporaryFile(suffix=".py") as config:
            key.write(Fernet.generate_key())
            key.flush()
            os.environ.update(
                DJANGO_SETTINGS_MODULE="pulpcore.app.settings",
                PULP_ENABLED_PLUGINS='["pulp_maven"]',
                PULP_DB_ENCRYPTION_KEY=key.name,
                PULP_SETTINGS=config.name,
            )
            import django
            with patch("django.db.backends.utils.CursorWrapper.execute",
                       side_effect=AssertionError("Unexpected database access")):
                django.setup()
                from aiohttp import web
                from pulpcore.plugin.content import app
                from pulp_maven.app.models import MavenDistribution

                async def probe(request):
                    return web.Response(text="ok")

                assert not app.frozen
                app.add_routes([web.get("/pulp/maven-index/probe", probe)])
                assert any(
                    route.resource.get_info().get("path") == "/pulp/maven-index/probe"
                    for route in app.router.routes()
                )
                assert callable(MavenDistribution.content_handler)
        """
    )
    environment = os.environ.copy()
    # Do not read an operator's deployment settings in this import-only smoke test.
    for key in tuple(environment):
        if key.startswith("PULP_") or key == "DJANGO_SETTINGS_MODULE":
            environment.pop(key)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
