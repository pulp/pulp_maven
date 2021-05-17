from django.db import migrations, models, transaction
import django.db.models.deletion


def migrate_data_from_old_model_to_new_model_up(apps, schema_editor):
    """ Move objects from MavenDistribution to NewMavenDistribution."""
    MavenDistribution = apps.get_model('maven', 'MavenDistribution')
    NewMavenDistribution = apps.get_model('maven', 'NewMavenDistribution')
    for maven_distribution in MavenDistribution.objects.all():
        with transaction.atomic():
            NewMavenDistribution(
                pulp_id=maven_distribution.pulp_id,
                pulp_created=maven_distribution.pulp_created,
                pulp_last_updated=maven_distribution.pulp_last_updated,
                pulp_type=maven_distribution.pulp_type,
                name=maven_distribution.name,
                base_path=maven_distribution.base_path,
                content_guard=maven_distribution.content_guard,
                remote=maven_distribution.remote,
                repository_version=maven_distribution.repository_version,
                repository=maven_distribution.repository
            ).save()
            maven_distribution.delete()


def migrate_data_from_old_model_to_new_model_down(apps, schema_editor):
    """ Move objects from NewMavenDistribution to MavenDistribution."""
    MavenDistribution = apps.get_model('maven', 'MavenDistribution')
    NewMavenDistribution = apps.get_model('maven', 'NewMavenDistribution')
    for maven_distribution in NewMavenDistribution.objects.all():
        with transaction.atomic():
            MavenDistribution(
                pulp_id=maven_distribution.pulp_id,
                pulp_created=maven_distribution.pulp_created,
                pulp_last_updated=maven_distribution.pulp_last_updated,
                pulp_type=maven_distribution.pulp_type,
                name=maven_distribution.name,
                base_path=maven_distribution.base_path,
                content_guard=maven_distribution.content_guard,
                remote=maven_distribution.remote,
                repository_version=maven_distribution.repository_version,
                repository=maven_distribution.repository
            ).save()
            maven_distribution.delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('core', '0062_add_new_distribution_mastermodel'),
        ('maven', '0003_mavenrepository'),
    ]

    operations = [
        migrations.CreateModel(
            name='NewMavenDistribution',
            fields=[
                ('distribution_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, related_name='maven_mavendistribution', serialize=False, to='core.Distribution')),
            ],
            options={
                'default_related_name': '%(app_label)s_%(model_name)s',
            },
            bases=('core.distribution',),
        ),
        migrations.RunPython(
            code=migrate_data_from_old_model_to_new_model_up,
            reverse_code=migrate_data_from_old_model_to_new_model_down,
        ),
        migrations.DeleteModel(
            name='MavenDistribution',
        ),
        migrations.RenameModel(
            old_name='NewMavenDistribution',
            new_name='MavenDistribution',
        ),
    ]
