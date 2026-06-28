from django.db import migrations


def populate_sha256_and_migrate_metadata(apps, schema_editor):
    """Populate sha256 on existing MavenArtifact rows and migrate MavenMetadata into MavenArtifact."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE maven_mavenartifact ma
            SET sha256 = a.sha256
            FROM core_contentartifact ca
            JOIN core_artifact a ON a.pulp_id = ca.artifact_id
            WHERE ca.content_id = ma.content_ptr_id
              AND ma.sha256 = ''
            """
        )

        cursor.execute(
            """
            INSERT INTO maven_mavenartifact
                (content_ptr_id, group_id, artifact_id, version, filename, sha256, _pulp_domain_id)
            SELECT
                content_ptr_id, group_id, artifact_id, version, filename, sha256, _pulp_domain_id
            FROM maven_mavenmetadata
            """
        )

        cursor.execute(
            """
            UPDATE core_content
            SET pulp_type = 'maven.artifact'
            WHERE pulp_type = 'maven.metadata'
            """
        )

        cursor.execute("DELETE FROM maven_mavenmetadata")


def reverse_migration(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("maven", "0009_migrate_metadata_data"),
    ]

    operations = [
        migrations.RunPython(
            populate_sha256_and_migrate_metadata,
            reverse_migration,
        ),
    ]
