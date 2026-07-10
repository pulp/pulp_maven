from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0148_artifact_artifact_domain_size_index"),
        ("maven", "0008_mavenpublication_mavenrepository_autopublish"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="mavenrepository",
            name="autopublish",
        ),
        migrations.DeleteModel(
            name="MavenPublication",
        ),
    ]
