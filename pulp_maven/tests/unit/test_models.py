from django.test import TestCase

from pulp_maven.app.models import MavenContentMixin


class TestMavenContentMixin(TestCase):
    """Test MavenContentMixin version parsing."""

    def test_standard_version_format(self):
        """Test standard semantic version format (1.2.3)."""
        relative_path = "org/example/myapp/1.2.3/myapp-1.2.3.jar"
        group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
            relative_path
        )

        self.assertEqual(group_id, "org.example.myapp")
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

                self.assertEqual(group_id, "org.example.myapp")
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

                self.assertEqual(group_id, "org.example.myapp")
                self.assertEqual(artifact_id, "myapp")
                self.assertEqual(version, expected_version)
                self.assertEqual(filename, f"myapp-{expected_version}.jar")

    def test_version_with_multiple_qualifiers(self):
        """Test version with multiple qualifiers (rc.1-1.2.3, alpha-beta-1.2)."""
        test_cases = [
            ("org/example/myapp/rc.1-1.2.3/myapp-rc.1-1.2.3.jar", "rc.1-1.2.3"),
            ("org/example/myapp/alpha-beta-1.2/myapp-alpha-beta-1.2.jar", "alpha-beta-1.2"),
        ]

        for relative_path, expected_version in test_cases:
            with self.subTest(version=expected_version):
                group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
                    relative_path
                )

                self.assertEqual(group_id, "org.example.myapp")
                self.assertEqual(artifact_id, "myapp")
                self.assertEqual(version, expected_version)
                self.assertEqual(filename, f"myapp-{expected_version}.jar")

    def test_two_part_version(self):
        """Test two-part version format (1.2)."""
        relative_path = "org/example/myapp/1.2/myapp-1.2.jar"
        group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
            relative_path
        )

        self.assertEqual(group_id, "org.example.myapp")
        self.assertEqual(artifact_id, "myapp")
        self.assertEqual(version, "1.2")
        self.assertEqual(filename, "myapp-1.2.jar")

    def test_single_digit_version(self):
        """Test single digit version format (1)."""
        relative_path = "org/example/myapp/1/myapp-1.jar"
        group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
            relative_path
        )

        self.assertEqual(group_id, "org.example.myapp")
        self.assertEqual(artifact_id, "myapp")
        self.assertEqual(version, "1")
        self.assertEqual(filename, "myapp-1.jar")

    def test_no_version(self):
        """Test path without version (treated as part of group_id)."""
        relative_path = "org/example/myapp/myapp.jar"
        group_id, artifact_id, version, filename = MavenContentMixin.group_artifact_version_filename(
            relative_path
        )

        self.assertEqual(group_id, "org.example.myapp")
        self.assertEqual(artifact_id, "myapp")
        self.assertIsNone(version)
        self.assertEqual(filename, "myapp.jar")
