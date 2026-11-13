import hashlib
import re
import time
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from pulpcore.plugin.constants import TASK_FINAL_STATES, TASK_STATES
from pulpcore.plugin.models import Artifact, ContentArtifact, Task
from pulpcore.plugin.tasking import dispatch
from pulpcore.plugin.util import get_domain, get_prn

from pulp_maven.app.models import (
    MavenArtifact,
    MavenDistribution,
    MavenMetadata,
    MavenPackage,
    MavenPendingDeploy,
    MavenRepository,
)
from pulp_maven.app.tasks import aadd_and_remove, commit_pending_deploys

# How pulpcore names the commit task, to find one that is already waiting.
COMMIT_TASK_NAME = f"{commit_pending_deploys.__module__}.{commit_pending_deploys.__name__}"


def _commit_lock_key(repo):
    """A Postgres advisory lock key for dispatching a repository's commit task."""
    return int(hashlib.sha256(f"maven-deploy-commit:{repo.pk}".encode()).hexdigest()[:15], 16)


def has_task_completed(task):
    """
    Wait for a dispatched task to finish and report how it ended.

    The task runs right away when no other task holds the repository. Otherwise it waits in the
    queue for a worker, and the upload waits with it for up to MAVEN_DEPLOY_TASK_TIMEOUT
    seconds, so concurrent deploys to one repository take turns instead of being refused.

    Returns:
        bool: True if the task ended successfully.

    Raises:
        Exception: If an error occured during the task's runtime.
        Throttled: If the task was canceled or did not finish within the timeout.

    """
    timeout = settings.MAVEN_DEPLOY_TASK_TIMEOUT
    deadline = time.monotonic() + timeout
    delay = 0.05
    while task.state not in TASK_FINAL_STATES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # The task stays queued and still adds the content; a retried upload of the same
            # file replaces it.
            raise Throttled(wait=timeout)
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 1)
        task.refresh_from_db()
    if task.state == TASK_STATES.COMPLETED:
        task.delete()
        return True
    elif task.state == TASK_STATES.CANCELED:
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


class MavenApiViewSet(ViewSet):
    """
    ViewSet for interacting with maven deploy API
    """

    model = MavenRepository
    queryset = MavenRepository.objects.all()

    lookup_field = "name"

    # Authentication and permission classes are intentionally NOT overridden here so that this
    # endpoint honors the deployment-configured defaults (REST_FRAMEWORK). Authorization is driven
    # by the access policy below, evaluated by the configured permission class (AccessPolicyFromDB).
    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["retrieve"],
                "principal": "*",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.modify_mavenrepository",
                ],
            },
        ],
    }

    @classmethod
    def urlpattern(cls):
        """Mock NamedModelViewSet so this non-model ViewSet participates in RBAC access policies."""
        return "maven/deploy"

    def get_object(self):
        """Return the repository addressed by the URL path for object-level permission checks."""
        return get_object_or_404(
            MavenRepository, name=self.kwargs["name"], pulp_domain=get_domain()
        )

    def redirect_to_content_app(self, distribution, relative_path, request):
        scheme = request.META.get("HTTP_X_FORWARDED_PROTO", request.scheme)
        hostname = request.META.get("HTTP_X_FORWARDED_HOST", request.get_host())
        content_origin = f"{scheme}://{hostname}"
        return redirect(
            f"{content_origin}{settings.CONTENT_PATH_PREFIX}"
            f"{get_full_path(distribution.base_path)}/{relative_path}"
        )

    def get_repository_and_distributions(self, name):
        repository = get_object_or_404(MavenRepository, name=name, pulp_domain=get_domain())
        distribution = MavenDistribution.objects.filter(
            repository=repository, pulp_domain=get_domain()
        ).first()
        if distribution is None:
            raise Http404
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

    @extend_schema(exclude=True)
    def retrieve(self, request, name, path):
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
        return self.redirect_to_content_app(distro, relative_path, request)

    @extend_schema(exclude=True)
    def create(self, request, name, path):
        repo, distro = self.get_repository_and_distributions(name)

        # Determine if this is an artifact or metadata
        is_metadata = self.is_metadata(path)

        # Save the uploaded file as an artifact
        chunk = request.META["wsgi.input"]
        artifact = self.receive_artifact(chunk)

        # Create a MavenArtifact or MavenMetadata
        try:
            if is_metadata:
                content = MavenMetadata.init_from_artifact_and_relative_path(artifact, path)
            else:
                content = MavenArtifact.init_from_artifact_and_relative_path(artifact, path)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
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

        if not is_metadata and content.filename.endswith(".pom"):
            pkg, created = MavenPackage.objects.get_or_create(
                group_id=content.group_id,
                artifact_id=content.artifact_id,
                version=content.version,
                _pulp_domain=get_domain(),
            )
            if created or content.version.endswith("-SNAPSHOT"):
                pkg.update_from_pom(artifact)
                pkg.save()

        if settings.MAVEN_DEPLOY_BATCH:
            self.add_pending(repo, content, path)
            return Response(status=201)

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
            aadd_and_remove,
            exclusive_resources=[repo],
            immediate=True,
            deferred=True,
            kwargs={
                "repository_pk": str(repo.pk),
                "add_content_units": add_content_units,
                "remove_content_units": remove_content_units,
            },
        )

        if has_task_completed(dispatched_task):
            return Response(status=201)

    @staticmethod
    def add_pending(repo, content, relative_path):
        """Record a deployed file for the next batch and make sure a commit task will add it."""
        MavenPendingDeploy.objects.create(
            repository=repo, content=content, relative_path=relative_path
        )
        # Pending files are served from the moment they are recorded, so a cached answer for
        # this path (a 404 from before the upload) must not outlive it.
        repo.invalidate_cache()
        # A commit task still waiting to run will find this file. One that is already running
        # may have taken its batch before the file arrived, so then another one is dispatched.
        # The check and the dispatch hold a lock per repository, so uploads arriving together
        # dispatch one task between them rather than one each.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_commit_lock_key(repo)])
            commit_waiting = Task.objects.filter(
                name=COMMIT_TASK_NAME,
                state=TASK_STATES.WAITING,
                reserved_resources_record__contains=[get_prn(repo)],
            ).exists()
            if not commit_waiting:
                dispatch(
                    commit_pending_deploys,
                    exclusive_resources=[repo],
                    kwargs={"repository_pk": str(repo.pk)},
                )

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
