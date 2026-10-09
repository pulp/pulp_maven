"""Functional reproduction for pulp/pulp_maven#524.

When a path-index repository finalizes a version that contains an ambiguous or
noncanonical artifact path, ``checked_rows`` raises ``InvalidIndex``. Historically
the message was a bare ``"Ambiguous or noncanonical artifact path"`` that omitted
the offending ``relative_path`` (and the ``content_id``), which made the failing
task impossible to diagnose from the task record alone. The error must name the
offending path.

The ambiguous case is reproduced through normal API operations only: a Maven
artifact named ``index.html`` collides with the directory index page that
path-index generates for its parent directory, so two content units share a
single ``relative_path`` when the repository version is finalized.

Path indexing is only active on an S3-backed domain (``config.store`` rejects any
other backend before ``checked_rows`` runs), so the test skips on the filesystem
and Azure CI variants.
"""

import time
import uuid

import pytest

# Mirrors pulp_maven.app.path_index.config.S3_BACKENDS.
S3_BACKENDS = {
    "storages.backends.s3.S3Storage",
    "storages.backends.s3boto3.S3Boto3Storage",
}
TASK_TERMINAL_STATES = {"completed", "failed", "canceled", "skipped"}


@pytest.mark.parallel
def test_path_index_error_reports_offending_path(
    maven_artifact_api_client,
    maven_repo_factory,
    random_artifact_factory,
    pulpcore_bindings,
    pulp_settings,
):
    """The InvalidIndex raised during finalize must name the offending path."""
    if pulp_settings.STORAGES["default"]["BACKEND"] not in S3_BACKENDS:
        pytest.skip("Maven path indexes require an S3 storage backend on the domain")

    # A unique artifact segment keeps the MavenArtifact natural key distinct
    # across parallel workers; the index.html collision is independent of it.
    offending_path = f"com/example/{uuid.uuid4().hex}/1.0/index.html"
    repository = maven_repo_factory(pulp_labels={"path_index": "true"})
    artifact = random_artifact_factory(size=64)

    # Creating the artifact directly into the path-index repository finalizes a
    # new version; the generated directory index page collides with this
    # index.html artifact, so finalize fails with InvalidIndex.
    response = maven_artifact_api_client.create(
        artifact=artifact.pulp_href,
        relative_path=offending_path,
        repository=repository.pulp_href,
    )

    # Read the task directly rather than via monitor_task: the finalize failure is
    # the behavior under test, and reading task.error is stable across pulpcore
    # versions (monitor_task raises a PulpTaskError whose formatting is not).
    deadline = time.monotonic() + 300
    task = pulpcore_bindings.TasksApi.read(response.task)
    while task.state not in TASK_TERMINAL_STATES and time.monotonic() < deadline:
        time.sleep(1)
        task = pulpcore_bindings.TasksApi.read(response.task)

    assert task.state == "failed", (
        f"expected the path-index finalize task to fail, got state={task.state!r} "
        f"(error={task.error!r})"
    )
    description = (task.error or {}).get("description", "") or ""
    assert offending_path in description, (
        "InvalidIndex must name the offending relative_path so operators can "
        f"identify the bad content; got: {description!r}"
    )
