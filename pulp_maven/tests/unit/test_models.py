from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import call, patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone

from pulp_maven.app.models import bloom_filters, get_bloom_filter


def repository_with_filter(value=None):
    """Build the repository state used by the cache tests without touching the database."""
    labels = {"pulp_maven.bloom_filter": value} if value else {}
    return SimpleNamespace(
        pulp_id=uuid4(),
        pulp_labels=labels,
        pulp_last_updated=timezone.now(),
    )


class TestNothing(SimpleTestCase):
    """Test Nothing (placeholder)."""

    def test_nothing_at_all(self):
        """Test that the tests are running and that's it."""
        self.assertTrue(True)


class TestBloomFilterCache(SimpleTestCase):
    """Test loading repository Bloom filters."""

    def setUp(self):
        bloom_filters.clear()

    def tearDown(self):
        bloom_filters.clear()

    def test_reuses_cached_filter(self):
        """The same repository revision reuses its parsed Bloom filter."""
        repository = repository_with_filter("first-filter")

        with patch("pulp_maven.app.models.BloomFilter") as bloom_filter_class:
            first_filter = get_bloom_filter(repository)
            second_filter = get_bloom_filter(repository)

        self.assertIs(first_filter, second_filter)
        bloom_filter_class.assert_called_once_with(hex_string="first-filter")

    def test_reloads_filter_after_repository_update(self):
        """A repository update replaces its cached Bloom filter."""
        repository = repository_with_filter("first-filter")

        filters = [object(), object()]
        with patch("pulp_maven.app.models.BloomFilter", side_effect=filters) as bloom_filter_class:
            first_filter = get_bloom_filter(repository)
            repository.pulp_labels["pulp_maven.bloom_filter"] = "second-filter"
            repository.pulp_last_updated += timedelta(seconds=1)
            second_filter = get_bloom_filter(repository)

        self.assertIsNot(first_filter, second_filter)
        self.assertEqual(
            bloom_filter_class.call_args_list,
            [
                call(hex_string="first-filter"),
                call(hex_string="second-filter"),
            ],
        )

    def test_returns_none_without_filter(self):
        """A repository without the label has no Bloom filter."""
        repository = repository_with_filter()

        with patch("pulp_maven.app.models.BloomFilter") as bloom_filter_class:
            bloom_filter = get_bloom_filter(repository)

        self.assertIsNone(bloom_filter)
        bloom_filter_class.assert_not_called()
