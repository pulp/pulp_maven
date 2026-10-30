"""Shared fixtures for the unit suite.

Isolate every unit test from process-global Django storage state.

For the ``default`` domain, :meth:`Domain.get_storage` returns Django's
process-global ``default_storage`` -- a ``LazyObject`` wrapping the backend built
from ``settings.STORAGES["default"]``. Tests that mutate it (for example
``monkeypatch.setattr(domain.get_storage(), "location", ...)`` in
``test_fresh_namespace_rebuilds_incompatible_profile``) therefore mutate shared,
cross-test state.

That is normally undone on teardown, but pytest 9.2 (pytest-dev/pytest#10644)
changed ``monkeypatch`` so that undoing a patch of an attribute that is not in the
target's own ``__dict__`` -- which is how ``location`` looks through the
``LazyObject`` proxy -- *deletes* it instead of restoring it. The wrapped
``S3Storage`` is then left with no ``location`` at all, and every later
``artifact.save()`` raises ``AttributeError: 'S3Storage' object has no attribute
'location'`` -- but only when that test happens to run first. Rebuild the backend
around every test so collection order cannot matter.
"""

import pytest
from django.core.files.storage import default_storage, storages
from django.utils.functional import empty


def _rebuild_default_storage():
    # Drop the StorageHandler's cached (possibly-mutated) instance and force the
    # LazyObject to build a fresh, fully ``__init__``-ed backend on next access.
    storages._storages.pop("default", None)
    default_storage._wrapped = empty


@pytest.fixture(autouse=True)
def reset_default_storage():
    """Give each unit test a pristine process-global default storage."""
    _rebuild_default_storage()
    yield
    _rebuild_default_storage()
