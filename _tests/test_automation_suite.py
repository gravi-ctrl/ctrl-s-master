#!/usr/bin/env python3
"""
test_automation_suite.py — Unified cross-platform test suite.

Place in the _tests/ directory. Run with:
    tests.bat   (Windows)
    ./tests.sh  (Linux)

Design notes
------------
- Tests that require live credentials (Bitwarden, Raindrop) are intentionally
  omitted. Those are integration concerns, not unit/orchestration concerns.
- A dynamically generated 'dummy_worker.py' is used to simulate successful tasks.
- All status output is redirected to _tests/ so no files are written to the
  project root during testing.
- Both notification channels (Email + Telegram) are disabled in the fixture.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

# This file lives in _tests/; project root is one level up.
ROOT_DIR  = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT_DIR / "_tests"

# Add src/_tools to path so common_utils can be imported directly.
sys.path.insert(0, str(ROOT_DIR / "src" / "_tools"))
from common_utils import rotate_backups  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_master(*args: str) -> subprocess.CompletedProcess:
    """Spawn master_automation.py in a subprocess, inheriting the test env."""
    return subprocess.run([sys.executable, str(ROOT_DIR / "src" / "master_automation.py"), *args],
        capture_output=True, text=True,
    )


def _read_status() -> dict:
    """Parse the test-scoped status.json written by master_automation."""
    status_file = TESTS_DIR / "status_test.json"
    assert status_file.exists(), "status.json was never written — did the run crash before completion?"
    with open(status_file, encoding="utf-8") as f:
        return json.load(f)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def _session_cleanup():
    """Delete the shared temp directory once after ALL tests finish."""
    yield
    temp_dir = TESTS_DIR / "temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    # Clean up test-scoped status files
    for leftover in["status_test.json", "status_dashboard_test.md"]:
        f = TESTS_DIR / leftover
        if f.exists():
            f.unlink()


@pytest.fixture
def env(monkeypatch, _session_cleanup):
    """
    Prepare a clean, isolated environment before each test.
    """
    temp_dir = TESTS_DIR / "temp"

    # Per-test wipe of temp contents (not the dir itself)
    if temp_dir.exists():
        for item in temp_dir.iterdir():
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink()
    else:
        temp_dir.mkdir(parents=True, exist_ok=True)

    # ── Dummy Worker Script (Guaranteed Success) ──
    dummy_worker = temp_dir / "dummy_worker.py"
    dummy_worker.write_text("print('Dummy worker success')")

    # Mock sync source dirs (needed so sync tasks don't error on missing source)
    src_2fa = temp_dir / "src_2fa"
    src_bak = temp_dir / "src_bak"
    src_2fa.mkdir()
    src_bak.mkdir()

    # ── Redirect master_automation paths ──
    monkeypatch.setenv("AUTOMATION_ROOT",          str(ROOT_DIR))
    monkeypatch.setenv("STATUS_FILE",              str(TESTS_DIR / "status_test.json"))
    monkeypatch.setenv("STATUS_DASHBOARD_FILE",    str(TESTS_DIR / "status_dashboard_test.md"))
    monkeypatch.setenv("BW_VAULTS_DIR",            str(temp_dir))
    monkeypatch.setenv("RAINDROP_BACKUP_DESTINATION", str(temp_dir))
    monkeypatch.setenv("SYNC_2FA_SOURCE_DIR",      str(src_2fa))
    monkeypatch.setenv("SYNC_BACKUPS_SOURCE_DIR",  str(src_bak))
    monkeypatch.setenv("SYNC_2FA_DEST",            str(temp_dir / "2fa"))
    monkeypatch.setenv("SYNC_BACKUPS_DEST",        str(temp_dir / "backups"))

    # ── Point worker scripts to the Dummy Success Script ──
    monkeypatch.setenv("BW_EXPORT_SCRIPT_PATH",       str(dummy_worker))
    monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", str(dummy_worker))

    # ── Dummy credential placeholders (Prevents "SKIPPED" status) ──
    monkeypatch.setenv("RAINDROP_PERSONAL_API_TOKEN", "dummy_token")
    monkeypatch.setenv("BW_PERSONAL_CLIENT_ID_UUID",  "dummy_uuid")
    monkeypatch.setenv("BITWARDEN_PERSONAL_PASSWORD", "dummy_pwd")

    # ── Disable all notifications ──
    monkeypatch.setenv("EMAIL_SENDER",       "")
    monkeypatch.setenv("EMAIL_PASSWORD",     "")
    monkeypatch.setenv("EMAIL_RECIPIENT",    "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID",   "")

    # ── Default behaviour ──
    monkeypatch.setenv("CONTINUE_ON_ERROR",   "false")
    monkeypatch.setenv("AUTOMATION_TEST_MODE", "true")

    # Clean up test-scoped status files from any previous test
    for leftover in["status_test.json", "status_dashboard_test.md"]:
        f = TESTS_DIR / leftover
        if f.exists():
            f.unlink()

    yield temp_dir


# ── Unit Tests: rotate_backups ────────────────────────────────────────────────

class TestRotateBackups:
    def test_deletes_oldest_files_when_over_limit(self, env):
        backup_dir = env / "rotation_test"
        backup_dir.mkdir()
        for i in range(5):
            (backup_dir / f"backup_{i:03d}.zip").write_text(f"content {i}")
            time.sleep(0.02)
        rotate_backups(backup_dir, "*.zip", max_to_keep=3)
        remaining = sorted(backup_dir.glob("*.zip"))
        assert len(remaining) == 3
        assert remaining[0].name == "backup_002.zip"
        assert remaining[1].name == "backup_003.zip"
        assert remaining[2].name == "backup_004.zip"

    def test_does_nothing_when_under_limit(self, env):
        backup_dir = env / "rotation_under"
        backup_dir.mkdir()
        for i in range(2):
            (backup_dir / f"backup_{i}.zip").write_text("x")
        rotate_backups(backup_dir, "*.zip", max_to_keep=5)
        assert len(list(backup_dir.glob("*.zip"))) == 2

    def test_does_nothing_when_disabled(self, env):
        backup_dir = env / "rotation_disabled"
        backup_dir.mkdir()
        for i in range(10):
            (backup_dir / f"backup_{i}.zip").write_text("x")
        rotate_backups(backup_dir, "*.zip", max_to_keep=0)
        assert len(list(backup_dir.glob("*.zip"))) == 10

    def test_handles_empty_directory_gracefully(self, env):
        backup_dir = env / "rotation_empty"
        backup_dir.mkdir()
        rotate_backups(backup_dir, "*.zip", max_to_keep=3)


# ── Integration Tests: orchestration behaviour ────────────────────────────────

class TestOrchestration:

    def test_status_json_written_on_success(self, env):
        """A successful run creates a parseable status.json with SUCCESS status."""
        result = _run_master("run-tasks", "raindrop-personal")

        assert result.returncode == 0, f"Unexpected failure:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"

        status = _read_status()
        assert status["last_run_status"] == "SUCCESS"
        assert len(status["run_history"]) >= 1

        tasks = status["run_history"][0]["tasks_summary"]
        assert "raindrop_personal" in tasks
        assert tasks["raindrop_personal"]["status"] == "SUCCESS"
        assert tasks["raindrop_personal"]["duration"] >= 0

    def test_status_json_written_on_failure(self, env, monkeypatch):
        """A failed run creates a status.json with FAILURE status."""
        # Mock the worker script to a nonexistent path to trigger a deliberate failure
        monkeypatch.setenv("BW_EXPORT_SCRIPT_PATH", "src/_tools/nonexistent_script.py")

        result = _run_master("run-tasks", "export-personal")

        assert result.returncode == 1
        status = _read_status()
        assert status["last_run_status"] == "FAILURE"
        tasks = status["run_history"][0]["tasks_summary"]
        assert tasks["export_personal"]["status"] == "FAILURE"

    def test_status_dashboard_written(self, env):
        """A successful run also writes a human-readable status_dashboard.md."""
        _run_master("run-tasks", "raindrop-personal")

        dashboard = TESTS_DIR / "status_dashboard_test.md"
        assert dashboard.exists(), "status_dashboard.md was not created"
        content = dashboard.read_text(encoding="utf-8")
        assert "# Automation Status Dashboard" in content
        assert "SUCCESS" in content

    def test_failure_stops_run_by_default(self, env, monkeypatch):
        """
        With CONTINUE_ON_ERROR=false (the default), the run halts immediately
        after the first failing task. Subsequent tasks must not appear in status.
        """
        monkeypatch.setenv("BW_EXPORT_SCRIPT_PATH", "src/_tools/nonexistent_script.py")
        monkeypatch.setenv("CONTINUE_ON_ERROR", "false")

        # export-personal (broken) -> should stop -> raindrop-personal should never run
        result = _run_master("run-tasks", "export-personal", "raindrop-personal")

        assert result.returncode == 1
        status = _read_status()
        tasks = status["run_history"][0]["tasks_summary"]

        assert "export_personal" in tasks
        assert tasks["export_personal"]["status"] == "FAILURE"
        assert "raindrop_personal" not in tasks, (
            "raindrop_personal ran despite CONTINUE_ON_ERROR=false — run did not stop."
        )

    def test_failure_continues_when_configured(self, env, monkeypatch):
        """
        With CONTINUE_ON_ERROR=true, the run continues past a failing task.
        The overall exit code is still 1, but subsequent tasks do execute.
        """
        monkeypatch.setenv("BW_EXPORT_SCRIPT_PATH", "src/_tools/nonexistent_script.py")
        monkeypatch.setenv("CONTINUE_ON_ERROR", "true")

        # export-personal (broken) -> continue -> raindrop-personal (guaranteed success)
        result = _run_master("run-tasks", "export-personal", "raindrop-personal")

        assert result.returncode == 1, "Overall exit code must be 1 when any task fails."
        status = _read_status()
        tasks = status["run_history"][0]["tasks_summary"]

        assert tasks["export_personal"]["status"] == "FAILURE"
        assert "raindrop_personal" in tasks, (
            "raindrop_personal did not run — CONTINUE_ON_ERROR=true was not respected."
        )
        assert tasks["raindrop_personal"]["status"] == "SUCCESS"

    def test_run_history_accumulates(self, env):
        """Successive runs append entries to run_history (capped at 10)."""
        for _ in range(3):
            _run_master("run-tasks", "raindrop-personal")

        status = _read_status()
        assert len(status["run_history"]) == 3

    def test_run_history_capped_at_ten(self, env):
        """run_history never grows beyond the last 10 entries."""
        for _ in range(12):
            _run_master("run-tasks", "raindrop-personal")

        status = _read_status()
        assert len(status["run_history"]) == 10