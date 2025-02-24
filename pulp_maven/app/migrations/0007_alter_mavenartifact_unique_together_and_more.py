# Generated by Django 4.2.10 on 2025-02-24 20:58

from django.db import migrations, models
import django.db.models.deletion
import pulpcore.app.util


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0126_remoteartifact_failed_at'),
        ('maven', '0006_alter_mavenartifact_content_ptr_and_more'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='mavenartifact',
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name='mavenmetadata',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='mavenartifact',
            name='_pulp_domain',
            field=models.ForeignKey(default=pulpcore.app.util.get_domain_pk, on_delete=django.db.models.deletion.PROTECT, to='core.domain'),
        ),
        migrations.AddField(
            model_name='mavenmetadata',
            name='_pulp_domain',
            field=models.ForeignKey(default=pulpcore.app.util.get_domain_pk, on_delete=django.db.models.deletion.PROTECT, to='core.domain'),
        ),
        migrations.AlterUniqueTogether(
            name='mavenartifact',
            unique_together={('group_id', 'artifact_id', 'version', 'filename', '_pulp_domain')},
        ),
        migrations.AlterUniqueTogether(
            name='mavenmetadata',
            unique_together={('group_id', 'artifact_id', 'version', 'filename', 'sha256', '_pulp_domain')},
        ),
    ]
