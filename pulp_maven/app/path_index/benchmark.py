"""Run a synthetic filesystem benchmark without Django or a running Pulp server.

    python -m pulp_maven.app.path_index.benchmark --directory /mnt/efs/benchmark

This measures the index engine only, not upload, authentication, or CDN capacity.
"""

import argparse
import hashlib
import json
import platform
import random
import resource
import statistics
import time
from pathlib import Path
from uuid import uuid4

from .format import Entry, path_hash
from .store import IndexStore


def _path(index):
    return f"org/example/library/{index}/library-{index}.jar"


def _entry(index, revision=0):
    path = _path(index)
    digest = hashlib.sha256(f"{path}:{revision}".encode("utf-8")).hexdigest()
    return Entry.for_path(path, digest, 1024 + revision, 1700000000 + revision)


def _positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _latencies(samples):
    ordered = sorted(samples)
    return {
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "p99": ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))],
        "max": ordered[-1],
    }


def _emit(**data):
    print(json.dumps(data, sort_keys=True), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--entries", type=_positive, default=100000)
    parser.add_argument("--updates", type=_positive, default=1000)
    parser.add_argument("--paths-per-update", type=_positive, default=10)
    parser.add_argument("--lookups", type=_positive, default=10000)
    parser.add_argument("--chunk-size", type=_positive, default=65536)
    parser.add_argument("--compact-every", type=_positive, default=8)
    args = parser.parse_args(argv)
    if args.paths_per_update > args.entries:
        parser.error("--paths-per-update cannot exceed --entries")

    store = IndexStore(
        args.directory, str(uuid4()), str(uuid4()), chunk_size=args.chunk_size, max_segments=32
    )
    _emit(
        phase="start",
        directory=str(store.root),
        python=platform.python_version(),
        entries=args.entries,
        updates=args.updates,
        paths_per_update=args.paths_per_update,
    )
    started = time.perf_counter()
    manifest = store.create(str(uuid4()), (_entry(index) for index in range(args.entries)))
    base_seconds = time.perf_counter() - started
    base_bytes = manifest.segments[0].byte_size
    _emit(phase="base", seconds=base_seconds, bytes=base_bytes)

    expected = {}
    update_samples = []
    compaction_seconds = 0
    max_segments = 1
    delta_bytes = 0
    for revision in range(1, args.updates + 1):
        added, deleted = [], []
        for offset in range(args.paths_per_update):
            index = (revision * args.paths_per_update + offset) % args.entries
            if (revision + offset) % 5 == 0:
                deleted.append(path_hash(_path(index)))
                expected[index] = None
            else:
                value = _entry(index, revision)
                added.append(value)
                expected[index] = value
        started = time.perf_counter()
        manifest = store.update(str(uuid4()), manifest, added, deleted)
        update_samples.append((time.perf_counter() - started) * 1000)
        delta_bytes += manifest.segments[-1].byte_size
        max_segments = max(max_segments, len(manifest.segments))
        if revision % args.compact_every == 0 or len(manifest.segments) == store.max_segments:
            started = time.perf_counter()
            manifest = store.compact(manifest)
            compaction_seconds += time.perf_counter() - started
        if revision % 200 == 0:
            _emit(phase="updates", completed=revision, current_segments=len(manifest.segments))

    randomizer = random.Random(2423)
    found_samples, missing_samples = [], []
    with store.open(manifest) as view:
        for _ in range(args.lookups):
            index = randomizer.randrange(args.entries)
            path = _path(index)
            expected_entry = expected[index] if index in expected else _entry(index)
            started = time.perf_counter_ns()
            actual = view.lookup(path)
            found_samples.append((time.perf_counter_ns() - started) / 1000)
            if actual != expected_entry:
                raise RuntimeError("Indexed result differs from the synthetic workload")
            started = time.perf_counter_ns()
            actual = view.lookup(f"missing/{index}")
            missing_samples.append((time.perf_counter_ns() - started) / 1000)
            if actual is not None:
                raise RuntimeError("An absent path resolved to an artifact")

    _emit(
        phase="result",
        directory=str(store.root),
        base_seconds=base_seconds,
        base_bytes=base_bytes,
        update_latency_ms=_latencies(update_samples),
        compaction_seconds=compaction_seconds,
        delta_bytes=delta_bytes,
        segment_bytes_written=store.segment_bytes_written,
        manifest_bytes_written=store.manifest_bytes_written,
        flat_rewrite_bytes_for_comparison=base_bytes * (args.updates + 1),
        max_segments=max_segments,
        final_segments=len(manifest.segments),
        existing_path_lookup_us=_latencies(found_samples),
        missing_path_lookup_us=_latencies(missing_samples),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        note="Serial synthetic run; post-build page cache; no Django, S3, auth, or upload capacity claim.",
    )


if __name__ == "__main__":
    main()
