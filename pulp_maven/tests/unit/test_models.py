"""
Unit tests for MavenContentMixin version parsing.

These tests can run without Django installed by mocking Django dependencies.
They test the actual code from pulp_maven.app.models, not a copy.
"""
import sys
import unittest
from unittest.mock import MagicMock, Mock

# Create a proper mock for Django models with correct metaclass
class MockModel:
    """Mock base class for Django models."""
    pass

# Mock Django modules before importing
django_models_mock = MagicMock()
django_models_mock.Model = MockModel
django_models_mock.CharField = MagicMock
django_models_mock.ForeignKey = MagicMock
django_models_mock.PROTECT = MagicMock

pulpcore_models_mock = MagicMock()
pulpcore_models_mock.Content = MockModel
pulpcore_models_mock.Remote = MockModel
pulpcore_models_mock.Repository = MockModel
pulpcore_models_mock.Distribution = MockModel

sys.modules['django'] = MagicMock()
sys.modules['django.db'] = MagicMock()
sys.modules['django.db.models'] = django_models_mock
sys.modules['pulpcore'] = MagicMock()
sys.modules['pulpcore.plugin'] = MagicMock()
sys.modules['pulpcore.plugin.models'] = pulpcore_models_mock
sys.modules['pulpcore.plugin.util'] = MagicMock()

# Mock get_domain_pk function
sys.modules['pulpcore.plugin.util'].get_domain_pk = MagicMock(return_value=1)

# Now import the actual code from the module
from pulp_maven.app.models import MavenContentMixin


class TestMavenContentMixin(unittest.TestCase):
    """Test MavenContentMixin version parsing."""

    def test_standard_version_format(self):
        """Test standard semantic version format (1.2.3)."""
        relative_path = "org/example/myapp/1.2.3/myapp-1.2.3.jar"
        group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
            relative_path
        )

        self.assertEqual(group_id, "org.example")
        self.assertEqual(artifact_id, "myapp")
        self.assertEqual(version, "1.2.3")
        self.assertEqual(filename, "myapp-1.2.3.jar")

    def test_version_with_suffix(self):
        """Test version with suffix format (1.2.3-rc, 1.2.3-SNAPSHOT)."""
        test_cases = [
            ("org/example/myapp/1.2.3-rc/myapp-1.2.3-rc.jar", "1.2.3-rc"),
            ("org/example/myapp/1.2.3-SNAPSHOT/myapp-1.2.3-SNAPSHOT.jar", "1.2.3-SNAPSHOT"),
            ("org/example/myapp/1.2.3-beta1/myapp-1.2.3-beta1.jar", "1.2.3-beta1"),
        ]

        for relative_path, expected_version in test_cases:
            with self.subTest(version=expected_version):
                group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
                    relative_path
                )

                self.assertEqual(group_id, "org.example")
                self.assertEqual(artifact_id, "myapp")
                self.assertEqual(version, expected_version)
                self.assertEqual(filename, f"myapp-{expected_version}.jar")

    def test_version_with_prefix(self):
        """Test version with prefix format (rc-1.2.3, SNAPSHOT-1.2.3) - Artifactory compatibility."""
        test_cases = [
            ("org/example/myapp/rc-1.2.3/myapp-rc-1.2.3.jar", "rc-1.2.3"),
            ("org/example/myapp/SNAPSHOT-1.2.3/myapp-SNAPSHOT-1.2.3.jar", "SNAPSHOT-1.2.3"),
            ("org/example/myapp/beta1-1.2.3/myapp-beta1-1.2.3.jar", "beta1-1.2.3"),
            ("org/example/myapp/v1-1.2/myapp-v1-1.2.jar", "v1-1.2"),
        ]

        for relative_path, expected_version in test_cases:
            with self.subTest(version=expected_version):
                group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
                    relative_path
                )

                self.assertEqual(group_id, "org.example")
                self.assertEqual(artifact_id, "myapp")
                self.assertEqual(version, expected_version)
                self.assertEqual(filename, f"myapp-{expected_version}.jar")

if __name__ == '__main__':
    unittest.main()
