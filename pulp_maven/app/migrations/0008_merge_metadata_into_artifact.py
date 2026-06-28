from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0126_remoteartifact_failed_at"),
        ("maven", "0007_alter_mavenartifact_unique_together_and_more"),
    ]

    operations = [
        # Make version nullable on MavenArtifact
        migrations.AlterField(
            model_name="mavenartifact",
            name="version",
            field=models.CharField(max_length=255, null=True),
        ),
        # Add sha256 field to MavenArtifact (with empty default for existing rows)
        migrations.AddField(
            model_name="mavenartifact",
            name="sha256",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
    ]
