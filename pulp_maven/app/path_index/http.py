"""Indexed HTTP metadata with Pulp's public artifact streaming response."""

from email.utils import formatdate

from aiohttp import web
from asgiref.sync import sync_to_async
from django.conf import settings

from pulpcore.plugin.content import ArtifactResponse

S3_BACKENDS = {"storages.backends.s3.S3Storage", "storages.backends.s3boto3.S3Boto3Storage"}
SUPPORTED_BACKENDS = S3_BACKENDS | {"pulpcore.app.models.storage.FileSystem"}


def headers_for(entry, headers):
    headers.update(
        {
            "ETag": f'"{entry.artifact_sha256.hex()}"',
            "Last-Modified": formatdate(entry.last_modified, usegmt=True),
            "Cache-Control": "public, max-age=0, must-revalidate",
        }
    )
    return headers


def conditional_status(request, entry):
    digest = entry.artifact_sha256.hex()
    match = request.if_match
    if match and not any(e.value == "*" or (e.value == digest and not e.is_weak) for e in match):
        return 412
    if (
        not match
        and request.if_unmodified_since
        and entry.last_modified > request.if_unmodified_since.timestamp()
    ):
        return 412
    if request.if_none_match is not None:
        if any(e.value in {"*", digest} for e in request.if_none_match):
            return 304
    elif request.if_modified_since and entry.last_modified <= request.if_modified_since.timestamp():
        return 304
    return None


class IndexedArtifactResponse(ArtifactResponse):
    """Keep S3 redirects configurable; stream through the public Pulp response."""

    def __init__(self, artifact, entry, domain, path, headers, *, inline_html=False):
        super().__init__(artifact=artifact, headers=headers_for(entry, headers))
        self.entry, self.domain, self.path = entry, domain, path
        self.inline_html = inline_html
        if inline_html:
            self.content_type = "text/html"
            self.charset = "utf-8"
            self.headers["Content-Disposition"] = "inline"

    async def prepare(self, request):
        if self.prepared:
            return await web.StreamResponse.prepare(self, request)
        if status := conditional_status(request, self.entry):
            self.set_status(status)
            return await web.StreamResponse.prepare(self, request)
        self.headers["X-PULP-ARTIFACT-SIZE"] = str(self.entry.size)
        threshold = settings.MAVEN_PATH_INDEX_REDIRECT_THRESHOLD
        redirect = (
            not self.inline_html
            and self.domain.storage_class in S3_BACKENDS
            and (
                self.domain.redirect_to_object_storage
                or (threshold is not None and self.entry.size > threshold)
            )
        )
        if redirect:
            try:
                request.http_range
            except ValueError:
                self.set_status(416)
                self.headers["Content-Range"] = f"bytes */{self.entry.size}"
                return await web.StreamResponse.prepare(self, request)
            filename = self.path.rsplit("/", 1)[-1]
            self.headers["Content-Disposition"] = f"attachment;filename={filename}"
            parameters = {
                target: self.headers[name]
                for name, target in {
                    "Content-Type": "ResponseContentType",
                    "Content-Disposition": "ResponseContentDisposition",
                    "Content-Encoding": "ResponseContentEncoding",
                    "Content-Language": "ResponseContentLanguage",
                    "Cache-Control": "ResponseCacheControl",
                }.items()
                if name in self.headers
            }

            def url():
                return self.domain.get_storage().url(
                    self._artifact.file.name, parameters=parameters, http_method=request.method
                )

            self.headers["Location"] = await sync_to_async(url)()
            self.set_status(302)
            self.content_length = 0
            return await web.StreamResponse.prepare(self, request)
        return await super().prepare(request)
