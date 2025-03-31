import hashlib
import re
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.shortcuts import get_object_or_404, redirect
from django.db import IntegrityError

from rest_framework.exceptions import Throttled
from rest_framework.views import APIView
from rest_framework.response import Response

from pulp_maven.app.models import (
    MavenArtifact,
    MavenMetadata,
    MavenRepository,
    MavenDistribution,
)
from pulpcore.plugin.models import Artifact, ContentArtifact
from pulpcore.plugin.tasking import add_and_remove, dispatch
from pulpcore.plugin.util import get_domain


def has_task_completed(task):
    """
    Verify whether an immediate task ran properly.

    Returns:
        bool: True if the task ended successfully.

    Raises:
        Exception: If an error occured during the task's runtime.
        Throttled: If the task did not run due to resource constraints.

    """
    if task.state == "completed":
        task.delete()
        return True
    elif task.state == "canceled":
        raise Throttled()
    else:
        error = task.error
        task.delete()
        raise Exception(str(error))


def get_full_path(base_path, pulp_domain=None):
    if settings.DOMAIN_ENABLED:
        domain = pulp_domain or get_domain()
        return f"{domain.name}/{base_path}"
    return base_path


class PomResponse(Response):
    def __init__(self, pom_artifact, status=200):
        artifact = pom_artifact._artifacts.get()
        size = artifact.size
        headers = {
            "Content-Type": "application/xml",
            "Content-Length": size,
            "ETag": f"{{SHA1}}{artifact.sha1}",
        }
        super().__init__(headers=headers, status=status)


class MavenApiViewSet(APIView):
    """
    ViewSet for interacting with maven deploy API
    """

    model = MavenRepository
    queryset = MavenRepository.objects.all()

    lookup_field = "name"

    # Authentication disabled for now
    authentication_classes = []
    permission_classes = []

    def redirect_to_content_app(self, distribution, relative_path):
        return redirect(
            f"{settings.CONTENT_ORIGIN}{settings.CONTENT_PATH_PREFIX}"
            f"{get_full_path(distribution.base_path)}/{relative_path}"
        )

    def get_repository_and_distributions(self, name):
        repository = get_object_or_404(MavenRepository, name=name, pulp_domain=get_domain())
        distribution = get_object_or_404(
            MavenDistribution, repository=repository, pulp_domain=get_domain()
        )
        return repository, distribution

    @staticmethod
    def is_metadata(path):
        is_metadata = False
        pattern = r"\.(xml|xml\.sha1|xml\.md5|xml\.sha224|xml\.sha256|xml\.sha384|xml\.sha512)$"
        if re.search(pattern, path):
            is_metadata = True
        return is_metadata

    @staticmethod
    def maven_artifact_attrs_from_path(path):
        group_id, artifact_id, version, name = MavenArtifact.group_artifact_version_filename(path)

        attrs = dict(
            filename=name,
            version=version,
            artifact_id=artifact_id,
            group_id=group_id,
        )
        return attrs

    def get(self, request, name, path):
        """
        Responds to GET requests about manifests by reference
        """
        repo, distro = self.get_repository_and_distributions(name)

        kwargs = self.maven_artifact_attrs_from_path(path)
        kwargs["pk__in"] = repo.latest_version().content

        if self.is_metadata(path):
            model = MavenMetadata
        else:
            model = MavenArtifact
        content = get_object_or_404(model, **kwargs)
        relative_path = content.contentartifact_set.get().relative_path
        return self.redirect_to_content_app(distro, relative_path)

    def put(self, request, name, path):
        repo, distro = self.get_repository_and_distributions(name)

        # Determine if this is an artifact or metadata
        is_metadata = self.is_metadata(path)

        # Save the uploaded file as an artifact
        chunk = request.META["wsgi.input"]
        artifact = self.receive_artifact(chunk)

        # Create a MavenArtifact or MavenMetadata
        if is_metadata:
            content = MavenMetadata.init_from_artifact_and_relative_path(artifact, path)
        else:
            content = MavenArtifact.init_from_artifact_and_relative_path(artifact, path)
        try:
            content.save()
        except IntegrityError:
            if is_metadata:
                content = MavenMetadata.objects.get(sha256=content.sha256, pulp_domain=get_domain())
            else:
                content = MavenArtifact.objects.get(
                    group_id=content.group_id,
                    artifact_id=content.artifact_id,
                    version=content.version,
                    filename=content.filename,
                    pulp_domain=get_domain(),
                )
        ca = ContentArtifact(artifact=artifact, content=content, relative_path=path)
        try:
            ca.save()
        except IntegrityError:
            ca = ContentArtifact.objects.get(content=content, relative_path=path)
            if not ca.artifact:
                ca.artifact = artifact
                ca.save()

        if is_metadata:
            metadata_to_remove = MavenMetadata.objects.filter(
                pk__in=repo.latest_version().content.all(),
                group_id=content.group_id,
                artifact_id=content.artifact_id,
                version=content.version,
                filename=content.filename,
                pulp_domain=get_domain(),
            )
            remove_content_units = [str(c[0]) for c in metadata_to_remove.values_list("pk")]
        else:
            remove_content_units = []

        add_content_units = [str(content.pk)]

        dispatched_task = dispatch(
            add_and_remove,
            exclusive_resources=[repo],
            immediate=True,
            deferred=False,
            kwargs={
                "repository_pk": str(repo.pk),
                "add_content_units": add_content_units,
                "remove_content_units": remove_content_units,
            },
        )

        if has_task_completed(dispatched_task):
            return Response(status=201)

    @staticmethod
    def receive_artifact(chunk):
        """Handles assembling of a file as it's being uploaded."""
        with NamedTemporaryFile("ab") as temp_file:
            size = 0
            hashers = {}
            for algorithm in Artifact.DIGEST_FIELDS:
                hashers[algorithm] = getattr(hashlib, algorithm)()
            while True:
                subchunk = chunk.read(2000000)
                if not subchunk:
                    break
                temp_file.write(subchunk)
                size += len(subchunk)
                for algorithm in Artifact.DIGEST_FIELDS:
                    hashers[algorithm].update(subchunk)
            temp_file.flush()
            digests = {}
            for algorithm in Artifact.DIGEST_FIELDS:
                digests[algorithm] = hashers[algorithm].hexdigest()
            artifact = Artifact(file=temp_file.name, size=size, pulp_domain=get_domain(), **digests)
            try:
                artifact.save()
            except IntegrityError:
                artifact = Artifact.objects.get(sha256=artifact.sha256, pulp_domain=get_domain())
                artifact.touch()
            return artifact
