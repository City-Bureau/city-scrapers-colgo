"""Engine-level tests for GuardedDiffPipeline: each runs a real crawl with canned
responses in a subprocess (see tests/diff_guard_crawl.py)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from city_scrapers_core.constants import CANCELLED

SCRIPT = Path(__file__).parent / "diff_guard_crawl.py"


def crawl(tmp_path, mode, fallback):
    output = tmp_path / f"{mode}_{fallback}.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), mode, fallback, str(output)],
        check=True,
        timeout=60,
    )
    return json.loads(output.read_text())


@pytest.mark.parametrize("fallback", ["before", "after"])
def test_requests_scheduled_on_idle_are_diffed_before_previous_feed(tmp_path, fallback):
    result = crawl(tmp_path, "guarded", fallback)
    assert result["finish_reason"] == "finished"
    assert result["errors"] == []
    # Every fresh meeting is published once, with the previous _id, none cancelled
    assert sorted(item["_id"] for item in result["items"]) == sorted(
        item["_id"] for item in result["previous"]
    )
    assert not any(item["status"] == CANCELLED for item in result["items"])
    assert all(
        item["updated_at"] != "2026-01-01T00:00:00-08:00" for item in result["items"]
    )


def test_core_pipeline_cancels_when_requests_scheduled_on_idle(tmp_path):
    """The failure the guarded pipeline avoids, to show the harness catches it"""
    result = crawl(tmp_path, "core", "after")
    assert any(item["status"] == CANCELLED for item in result["items"])


def test_zero_scrape_crawl_trips_and_finishes(tmp_path):
    guarded = crawl(tmp_path, "guarded", "none")
    core = crawl(tmp_path, "core", "none")
    assert guarded["finish_reason"] == "finished"
    assert len(guarded["errors"]) == 1
    assert guarded["items"] == guarded["previous"]
    # Waiting for a quiet idle pass costs at most one extra 5 second idle cycle
    assert guarded["elapsed"] - core["elapsed"] < 7
