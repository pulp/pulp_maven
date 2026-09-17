"""Check that the development benchmark exercises and reports a complete run."""

import json

import pytest

from pulp_maven.app.path_index.benchmark import main


def test_small_benchmark(tmp_path, capsys):
    main(
        [
            "--directory",
            str(tmp_path),
            "--entries",
            "50",
            "--updates",
            "10",
            "--paths-per-update",
            "5",
            "--lookups",
            "20",
            "--compact-every",
            "2",
        ]
    )
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result = reports[-1]
    assert result["phase"] == "result"
    assert result["base_bytes"] == 16 + 50 * 64
    assert result["segment_bytes_written"] > result["base_bytes"]
    assert result["manifest_bytes_written"] > 0
    assert result["max_segments"] <= 32
    assert result["final_segments"] < 11
    for name in ("update_latency_ms", "existing_path_lookup_us", "missing_path_lookup_us"):
        assert 0 <= result[name]["p50"] <= result[name]["p95"] <= result[name]["p99"]


def test_benchmark_rejects_duplicate_update_paths(tmp_path):
    with pytest.raises(SystemExit):
        main(["--directory", str(tmp_path), "--entries", "2", "--paths-per-update", "3"])
