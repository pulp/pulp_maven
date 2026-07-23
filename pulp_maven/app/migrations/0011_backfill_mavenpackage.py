from django.db import migrations


def create_packages_from_poms(apps, schema_editor):
    """Create MavenPackage for every existing POM file.

    Packages are created globally but NOT added to repository versions.
    They will appear in repo versions on the next finalize_new_version call.
    """
    # Intentional import of app code: parse_pom_metadata is a pure function with no model deps.
    from pulp_maven.app.pom import parse_pom_metadata

    MavenArtifact = apps.get_model("maven", "MavenArtifact")
    MavenPackage = apps.get_model("maven", "MavenPackage")
    ContentArtifact = apps.get_model("core", "ContentArtifact")

    pom_gavs = (
        MavenArtifact.objects.filter(filename__endswith=".pom")
        .values("group_id", "artifact_id", "version", "_pulp_domain")
        .distinct()
    )

    for gav in pom_gavs.iterator(chunk_size=500):
        pkg, created = MavenPackage.objects.get_or_create(
            group_id=gav["group_id"],
            artifact_id=gav["artifact_id"],
            version=gav["version"],
            _pulp_domain_id=gav["_pulp_domain"],
        )

        if not created:
            continue

        pom_filename = f"{gav['artifact_id']}-{gav['version']}.pom"
        pom_content = MavenArtifact.objects.filter(
            group_id=gav["group_id"],
            artifact_id=gav["artifact_id"],
            version=gav["version"],
            filename=pom_filename,
            _pulp_domain_id=gav["_pulp_domain"],
        ).first()

        if not pom_content:
            continue

        ca = (
            ContentArtifact.objects.filter(content=pom_content.content_ptr_id)
            .select_related("artifact")
            .first()
        )

        if not ca or not ca.artifact:
            continue

        try:
            with ca.artifact.file.open("rb") as f:
                meta = parse_pom_metadata(f)
        except Exception:
            continue

        if meta:
            pkg.name = meta["name"]
            pkg.description = meta["description"]
            pkg.packaging = meta["packaging"]
            pkg.url = meta["url"]
            pkg.licenses = meta["licenses"]
            pkg.dependencies = meta["dependencies"]
            pkg.scm_url = meta["scm_url"]
            pkg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("maven", "0010_mavenpackage"),
    ]

    operations = [
        migrations.RunPython(
            create_packages_from_poms,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
