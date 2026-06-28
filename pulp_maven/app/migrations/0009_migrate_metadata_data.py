from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("maven", "0008_merge_metadata_into_artifact"),
    ]

    operations = [
        # Drop unique_together constraints before data migration
        migrations.AlterUniqueTogether(
            name="mavenartifact",
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name="mavenmetadata",
            unique_together=set(),
        ),
    ]
