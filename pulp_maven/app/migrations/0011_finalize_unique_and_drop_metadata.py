from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("maven", "0010_populate_and_migrate"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="mavenartifact",
            unique_together={
                ("group_id", "artifact_id", "version", "filename", "sha256", "_pulp_domain")
            },
        ),
        migrations.DeleteModel(
            name="MavenMetadata",
        ),
    ]
