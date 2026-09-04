import django.contrib.postgres.indexes
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("maven", "0012_add_rbac_permissions"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="mavenpackage",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["group_id"],
                name="maven_pkg_group_id_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        migrations.AddIndex(
            model_name="mavenpackage",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["artifact_id"],
                name="maven_pkg_artifact_id_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
