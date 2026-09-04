from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import AddIndexConcurrently, TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False  # required for CONCURRENTLY

    dependencies = [
        ("maven", "0013_mavenindexpage"),
    ]

    operations = [
        TrigramExtension(),
        AddIndexConcurrently(
            model_name="mavenpackage",
            index=GinIndex(
                fields=["group_id"],
                name="maven_pkg_group_id_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        AddIndexConcurrently(
            model_name="mavenpackage",
            index=GinIndex(
                fields=["artifact_id"],
                name="maven_pkg_artifact_id_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
