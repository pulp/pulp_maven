from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("maven", "0011_backfill_mavenpackage"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="mavendistribution",
            options={
                "default_related_name": "%(app_label)s_%(model_name)s",
                "permissions": [
                    (
                        "manage_roles_mavendistribution",
                        "Can manage roles on Maven distribution",
                    ),
                ],
            },
        ),
        migrations.AlterModelOptions(
            name="mavenremote",
            options={
                "default_related_name": "%(app_label)s_%(model_name)s",
                "permissions": [
                    ("manage_roles_mavenremote", "Can manage roles on Maven remote"),
                ],
            },
        ),
        migrations.AlterModelOptions(
            name="mavenrepository",
            options={
                "default_related_name": "%(app_label)s_%(model_name)s",
                "permissions": [
                    (
                        "modify_mavenrepository",
                        "Can modify content in Maven repository",
                    ),
                    (
                        "manage_roles_mavenrepository",
                        "Can manage roles on Maven repository",
                    ),
                    (
                        "repair_mavenrepository",
                        "Can repair Maven repository metadata",
                    ),
                ],
            },
        ),
    ]
