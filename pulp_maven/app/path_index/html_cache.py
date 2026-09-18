"""Content-addressed HTML cache shared by workers; live readers pin their files."""

import fcntl
import hashlib
import os
from contextlib import ExitStack
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings

from .config import profile
from .format import InvalidIndex
from .s3 import CacheFull, _lock


def open_html(entry, domain, artifact):
    """Return (stream, owner); owner.close releases the file and shared lock."""
    budget = settings.MAVEN_PATH_INDEX_HTML_BYTES
    owner = ExitStack()
    if entry.size > budget or domain.storage_class == "pulpcore.app.models.storage.FileSystem":
        return owner.enter_context(artifact.file.open("rb")), owner
    root = Path(settings.MAVEN_PATH_INDEX_CACHE_DIR) / "html"
    directory = root / profile(domain)
    directory.mkdir(parents=True, exist_ok=True)
    digest = entry.artifact_sha256.hex()
    path = directory / f"{digest}.html"
    lock = directory / f"{digest}.lock"
    try:
        while True:
            with ExitStack() as pin:
                pin.enter_context(_lock(lock, fcntl.LOCK_SH, 30))
                if path.exists() and path.stat().st_size == entry.size:
                    os.utime(path, None)
                    stream = pin.enter_context(path.open("rb"))
                    owner.enter_context(pin.pop_all())
                    return stream, owner
            with _lock(lock, fcntl.LOCK_EX, 30):
                if path.exists():
                    if path.stat().st_size == entry.size:
                        continue
                    path.unlink()
                with _lock(root / "fill.lock", fcntl.LOCK_EX, 30):
                    # A dead loader releases the global lock; its temp file is disposable.
                    for temporary in root.glob("*/html-*.tmp"):
                        temporary.unlink()
                    paths = list(root.glob("*/*.html"))
                    used = sum(p.stat().st_size for p in paths)
                    for candidate in sorted(paths, key=lambda p: p.stat().st_mtime_ns):
                        if used + entry.size <= budget:
                            break
                        with candidate.with_suffix(".lock").open("a+b") as held:
                            try:
                                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                continue
                            used -= candidate.stat().st_size
                            candidate.unlink()
                    if used + entry.size > budget:
                        raise CacheFull("HTML cache is full of live readers")
                    with NamedTemporaryFile(
                        dir=directory, prefix="html-", suffix=".tmp", delete=False
                    ) as output:
                        temporary = Path(output.name)
                        try:
                            hasher = hashlib.sha256()
                            remaining = entry.size
                            with artifact.file.open("rb") as source:
                                while remaining:
                                    chunk = source.read(min(256 * 1024, remaining))
                                    if not chunk:
                                        raise InvalidIndex("Truncated HTML artifact")
                                    remaining -= len(chunk)
                                    hasher.update(chunk)
                                    output.write(chunk)
                                if source.read(1) or hasher.digest() != entry.artifact_sha256:
                                    raise InvalidIndex("HTML artifact does not match its digest")
                            output.flush()
                            os.fsync(output.fileno())
                            os.replace(temporary, path)
                        finally:
                            temporary.unlink(missing_ok=True)
    except (CacheFull, OSError):
        owner.close()
        owner = ExitStack()
        return owner.enter_context(artifact.file.open("rb")), owner
    except BaseException:
        owner.close()
        raise
