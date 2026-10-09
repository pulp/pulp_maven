from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("maven", "0014_mavenpackage_trgm_indexes")]

    operations = [
        migrations.AddField(
            model_name="mavenremote",
            name="metadata_cache_ttl",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
