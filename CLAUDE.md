# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`pulp-maven` is a [Pulp](https://pulpproject.org/) plugin that adds Maven repository management to Pulp. It extends `pulpcore` (required dependency `>=3.73.0`) and follows the Pulp plugin architecture.

## Commands

### Linting
```bash
pip install -r lint_requirements.txt
black --check --diff .   # format check (line-length: 100)
flake8                   # linting (config in .flake8)
```

### Unit Tests
Unit tests run against a live Django environment (requires a configured pulpcore installation):
```bash
pytest -v -r sx --color=yes --suppress-no-test-exit-code -p no:pulpcore --durations=20 --pyargs pulp_maven.tests.unit
```

To run a single unit test:
```bash
pytest -v --pyargs pulp_maven.tests.unit::TestNothing::test_nothing_at_all
```

### Functional Tests
Functional tests require a running Pulp instance (set up via CI Docker container) and use the generated Python client bindings:
```bash
pytest -v --timeout=300 -r sx --color=yes --suppress-no-test-exit-code --durations=20 --pyargs pulp_maven.tests.functional -m parallel -n 8
pytest -v --timeout=300 -r sx --color=yes --suppress-no-test-exit-code --durations=20 --pyargs pulp_maven.tests.functional -m 'not parallel'
```

### Migration Check
```bash
django-admin makemigrations maven --check --dry-run
```

## Architecture

### Plugin Structure
The plugin registers itself via the `pulpcore.plugin` entry point in `pyproject.toml`. The app config is in `pulp_maven/app/__init__.py` (`domain_compatible = True`).

### Content Types (`pulp_maven/app/models.py`)
- **`MavenArtifact`** — Represents a single binary file (JAR, POM, etc.) in a Maven repo. Uniquely identified by `(group_id, artifact_id, version, filename, _pulp_domain)`. Path parsing logic in `MavenContentMixin.group_artifact_version_filename()` converts `group/id/artifact/version/file` paths into structured fields.
- **`MavenMetadata`** — Represents XML metadata files (`maven-metadata.xml` and checksum variants). Unlike `MavenArtifact`, it includes a `sha256` field in its unique constraint so metadata can be updated (new version replaces old).
- **`MavenRemote`** — Overrides `get_remote_artifact_content_type()` to route `.xml`/checksum files to `MavenMetadata` and everything else to `MavenArtifact`.
- **`MavenRepository`** — Declares `CONTENT_TYPES = [MavenArtifact, MavenMetadata]`.
- **`MavenDistribution`** — Supports a `remote` field for pull-through caching.

### Maven Deploy API (`pulp_maven/app/maven_deploy_api.py`)
A custom `APIView` (no authentication enforced) mounted at `^pulp/maven/<name>/<path>$`:
- **GET**: Looks up content in the latest repository version and redirects to the Pulp content app.
- **PUT**: Receives uploaded files, creates `Artifact`/`ContentArtifact`/`MavenArtifact` or `MavenMetadata` records, then dispatches `aadd_and_remove` as an immediate task to update the repository version.

### Viewsets (`pulp_maven/app/viewsets.py`)
Standard Pulp CRUD viewsets for all models. `MavenRepositoryViewSet` adds an `add_cached_content` action that dispatches the `add_cached_content_to_repository` task to save content that was streamed via a remote into a new repository version.

### Tasks (`pulp_maven/app/tasks/__init__.py`)
- **`aadd_and_remove`** — Async wrapper around `pulpcore`'s `add_and_remove`, used by the deploy API for immediate (non-deferred) task dispatch.
- **`add_cached_content_to_repository`** — Creates a new repository version by finding `RemoteArtifact` records created since the last version and adding their content.

### URL Registration (`pulp_maven/app/urls.py`)
Mounts the deploy API at `^pulp/maven/` with optional domain prefix when `DOMAIN_ENABLED` is set.

## Changelog

Non-trivial changes require a changelog entry. Add a file to `CHANGES/` named `<issue-number>.<type>` where type is one of: `feature`, `bugfix`, `doc`, `removal`, `deprecation`, `misc`. Managed by `towncrier`; output goes to `CHANGES.md`.

## Important Conventions

- **CI files are auto-generated** by [plugin_template](https://github.com/pulp/plugin_template). Files in `.github/workflows/`, `.ci/`, and several root-level config files carry `# WARNING: DO NOT EDIT!` headers — update them via `./plugin-template --github pulp_maven` instead.
- **Pulpcore imports** must only use the public `pulpcore.plugin` API (enforced by `.ci/scripts/check_pulpcore_imports.sh`).
- **Line length**: 100 characters (both `black` and `flake8`).
- **Migrations** live in `pulp_maven/app/migrations/` and must be committed; the CI checks for uncommitted migrations.
