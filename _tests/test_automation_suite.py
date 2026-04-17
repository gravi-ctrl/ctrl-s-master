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
- The 'convert-kdbx' task is used as a guaranteed-success stand-in wherever
  a working task is needed: when no vaults/json dir exists it returns (True, "")
  immediately without needing any credentials or external tools.
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
from common_utils import rotate_backups  # noqa: E402  (import after path manipulation)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_master(*args: str) -> subprocess.CompletedProcess:
    """Spawn master_automation.py in a subprocess, inheriting the test env."""
    return subprocess.run(
        [sys.executable, str(ROOT_DIR / "src" / "master_automation.py"), *args],
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
    for leftover in ["status_test.json", "status_dashboard_test.md"]:
        f = TESTS_DIR / leftover
        if f.exists():
            f.unlink()


@pytest.fixture
def env(monkeypatch, _session_cleanup):
    """
    Prepare a clean, isolated environment before each test.

    - Wipes and recreates _tests/temp/
    - Redirects every path that master_automation.py writes to
    - Disables both notification channels
    - Points script paths to the correct (unified) worker scripts
    """
    temp_dir = TESTS_DIR / "temp"

    # Per-test wipe of temp contents (not the dir itself)
    if temp_dir.exists():
        for item in temp_dir.iterdir():
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink()
    else:
        temp_dir.mkdir(parents=True, exist_ok=True)

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

    # ── Correct (unified) worker script paths ──
    monkeypatch.setenv("BW_EXPORT_SCRIPT_PATH",       "src/_tools/bitwarden_exporter.py")
    monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/raindrop_backup.py")

    # ── Dummy credential placeholders ──
    # These allow tasks that do an env-var check to pass the check without real
    # credentials. convert-kdbx checks for these before checking if json/ exists,
    # so without them it errors out before reaching the "no json dir → skip" shortcut.
    monkeypatch.setenv("KDBX_PERSONAL_PASSWORD",  "test_placeholder")
    monkeypatch.setenv("KDBX_WORK_PASSWORD",       "test_placeholder")

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
    for leftover in ["status_test.json", "status_dashboard_test.md"]:
        f = TESTS_DIR / leftover
        if f.exists():
            f.unlink()

    yield temp_dir


# ── Unit Tests: rotate_backups ────────────────────────────────────────────────

class TestRotateBackups:
    """
    Pure unit tests for common_utils.rotate_backups.
    No subprocesses, no env vars, no credentials needed.
    """

    def test_deletes_oldest_files_when_over_limit(self, env):
        """Keeps the N newest files and removes the rest."""
        backup_dir = env / "rotation_test"
        backup_dir.mkdir()

        # Create 5 files with distinct mtimes
        for i in range(5):
            (backup_dir / f"backup_{i:03d}.zip").write_text(f"content {i}")
            time.sleep(0.02)  # ensure distinct modification times

        rotate_backups(backup_dir, "*.zip", max_to_keep=3)

        remaining = sorted(backup_dir.glob("*.zip"))
        assert len(remaining) == 3, f"Expected 3 files, got {[f.name for f in remaining]}"
        # The 3 newest (indices 2, 3, 4) must survive
        assert remaining[0].name == "backup_002.zip"
        assert remaining[1].name == "backup_003.zip"
        assert remaining[2].name == "backup_004.zip"

    def test_does_nothing_when_under_limit(self, env):
        """Does not delete anything when file count is within the limit."""
        backup_dir = env / "rotation_under"
        backup_dir.mkdir()
        for i in range(2):
            (backup_dir / f"backup_{i}.zip").write_text("x")

        rotate_backups(backup_dir, "*.zip", max_to_keep=5)

        assert len(list(backup_dir.glob("*.zip"))) == 2

    def test_does_nothing_when_disabled(self, env):
        """max_to_keep=0 means rotation is off — all files are preserved."""
        backup_dir = env / "rotation_disabled"
        backup_dir.mkdir()
        for i in range(10):
            (backup_dir / f"backup_{i}.zip").write_text("x")

        rotate_backups(backup_dir, "*.zip", max_to_keep=0)

        assert len(list(backup_dir.glob("*.zip"))) == 10

    def test_handles_empty_directory_gracefully(self, env):
        """No files to rotate should not raise any exception."""
        backup_dir = env / "rotation_empty"
        backup_dir.mkdir()
        rotate_backups(backup_dir, "*.zip", max_to_keep=3)  # must not raise


# ── Integration Tests: orchestration behaviour ────────────────────────────────

class TestOrchestration:
    """
    Tests for master_automation.py's orchestration logic.

    All tests use the 'convert-kdbx' task as a guaranteed-success stand-in:
    when no vaults/json directory exists it returns (True, "") immediately
    without requiring any credentials or external tools.

    Broken tasks are simulated by pointing a script path env var at a file
    that does not exist — this causes subprocess.CalledProcessError inside
    run_command(), which master_automation correctly treats as task failure.
    """

    def test_status_json_written_on_success(self, env):
        """A successful run creates a parseable status.json with SUCCESS status."""
        result = _run_master("run-tasks", "convert-kdbx")

        assert result.returncode == 0, f"Unexpected failure:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"

        status = _read_status()
        assert status["last_run_status"] == "SUCCESS"
        assert len(status["run_history"]) >= 1

        tasks = status["run_history"][0]["tasks_summary"]
        assert "convert_json_to_kdbx" in tasks
        assert tasks["convert_json_to_kdbx"]["status"] == "SUCCESS"
        assert tasks["convert_json_to_kdbx"]["duration"] >= 0

    def test_status_json_written_on_failure(self, env, monkeypatch):
        """A failed run creates a status.json with FAILURE status."""
        monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/nonexistent_script.py")

        result = _run_master("run-tasks", "raindrop-backup")

        assert result.returncode == 1
        status = _read_status()
        assert status["last_run_status"] == "FAILURE"
        tasks = status["run_history"][0]["tasks_summary"]
        assert tasks["raindrop_backup"]["status"] == "FAILURE"

    def test_status_dashboard_written(self, env):
        """A successful run also writes a human-readable status_dashboard.md."""
        _run_master("run-tasks", "convert-kdbx")

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
        monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/nonexistent_script.py")
        monkeypatch.setenv("CONTINUE_ON_ERROR", "false")

        # raindrop-backup (broken) → should stop → convert-kdbx should never run
        result = _run_master("run-tasks", "raindrop-backup", "convert-kdbx")

        assert result.returncode == 1
        status = _read_status()
        tasks = status["run_history"][0]["tasks_summary"]

        assert "raindrop_backup" in tasks
        assert tasks["raindrop_backup"]["status"] == "FAILURE"
        assert "convert_json_to_kdbx" not in tasks, (
            "convert-kdbx ran despite CONTINUE_ON_ERROR=false — run did not stop."
        )

    def test_failure_continues_when_configured(self, env, monkeypatch):
        """
        With CONTINUE_ON_ERROR=true, the run continues past a failing task.
        The overall exit code is still 1, but subsequent tasks do execute.
        """
        monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/nonexistent_script.py")
        monkeypatch.setenv("CONTINUE_ON_ERROR", "true")

        # raindrop-backup (broken) → continue → convert-kdbx (guaranteed success)
        result = _run_master("run-tasks", "raindrop-backup", "convert-kdbx")

        assert result.returncode == 1, "Overall exit code must be 1 when any task fails."
        status = _read_status()
        tasks = status["run_history"][0]["tasks_summary"]

        assert tasks["raindrop_backup"]["status"] == "FAILURE"
        assert "convert_json_to_kdbx" in tasks, (
            "convert-kdbx did not run — CONTINUE_ON_ERROR=true was not respected."
        )
        assert tasks["convert_json_to_kdbx"]["status"] == "SUCCESS"

    def test_run_history_accumulates(self, env):
        """Successive runs append entries to run_history (capped at 10)."""
        for _ in range(3):
            _run_master("run-tasks", "convert-kdbx")

        status = _read_status()
        assert len(status["run_history"]) == 3

    def test_run_history_capped_at_ten(self, env):
        """run_history never grows beyond the last 10 entries."""
        for _ in range(12):
            _run_master("run-tasks", "convert-kdbx")

        status = _read_status()
        assert len(status["run_history"]) == 10
