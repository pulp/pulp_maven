# Generated by Django 4.2.1 on 2023-05-17 07:54

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0106_alter_artifactdistribution_distribution_ptr_and_more"),
        ("maven", "0005_mavenmetadata"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mavenartifact",
            name="content_ptr",
            field=models.OneToOneField(
                auto_created=True,
                on_delete=django.db.models.deletion.CASCADE,
                parent_link=True,
                primary_key=True,
                serialize=False,
                to="core.content",
            ),
        ),
        migrations.AlterField(
            model_name="mavendistribution",
            name="distribution_ptr",
            field=models.OneToOneField(
                auto_created=True,
                on_delete=django.db.models.deletion.CASCADE,
                parent_link=True,
                primary_key=True,
                serialize=False,
                to="core.distribution",
            ),
        ),
        migrations.AlterField(
            model_name="mavenmetadata",
            name="content_ptr",
            field=models.OneToOneField(
                auto_created=True,
                on_delete=django.db.models.deletion.CASCADE,
                parent_link=True,
                primary_key=True,
                serialize=False,
                to="core.content",
            ),
        ),
        migrations.AlterField(
            model_name="mavenremote",
            name="remote_ptr",
            field=models.OneToOneField(
                auto_created=True,
                on_delete=django.db.models.deletion.CASCADE,
                parent_link=True,
                primary_key=True,
                serialize=False,
                to="core.remote",
            ),
        ),
        migrations.AlterField(
            model_name="mavenrepository",
            name="repository_ptr",
            field=models.OneToOneField(
                auto_created=True,
                on_delete=django.db.models.deletion.CASCADE,
                parent_link=True,
                primary_key=True,
                serialize=False,
                to="core.repository",
            ),
        ),
    ]
