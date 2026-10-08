from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("maven", "0014_mavenpackage_trgm_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="mavenremote",
            name="exclude_group_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
