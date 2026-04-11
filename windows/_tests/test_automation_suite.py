import os
import sys
import subprocess
import shutil
from pathlib import Path
import pytest

# Define the project's root directory, which is one level up from the '_tests' folder.
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


# --- Fixtures: Reusable Setup and Teardown for Tests ---

@pytest.fixture(scope="session")
def session_cleanup():
    """
    A session-scoped fixture to handle cleanup after ALL tests are complete.
    """
    yield
    print("\nSESSION TEARDOWN: Cleaning up temporary test directory...")
    temp_dir = ROOT_DIR / "_tests" / "temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("Temporary directory removed.")


@pytest.fixture
def test_environment(monkeypatch, session_cleanup):
    """
    A pytest fixture to prepare a clean, safe test environment before EACH test.
    """
    temp_dir = ROOT_DIR / "_tests" / "temp"
    
    if temp_dir.exists():
        for item in temp_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
    else:
        temp_dir.mkdir(parents=True, exist_ok=True)
    
    monkeypatch.setenv("AUTOMATION_TEST_MODE", "true")
    monkeypatch.setenv("STATUS_FILE", str(ROOT_DIR / "_tests" / "status_test.json"))
    monkeypatch.setenv("STATUS_DASHBOARD_FILE", str(ROOT_DIR / "_tests" / "status_dashboard_test.md"))
    monkeypatch.setenv("BW_VAULTS_DIR", str(temp_dir))
    monkeypatch.setenv("RAINDROP_BACKUP_DESTINATION", str(temp_dir)) # Point Raindrop to temp
    monkeypatch.setenv("EMAIL_SENDER", "") # Disable emails
    monkeypatch.setenv("CONTINUE_ON_ERROR", "false") # Default to stopping on error

    monkeypatch.setenv("BW_EXPORT_SCRIPT_PATH", "src/_tools/bitwarden-exporter.py")
    monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/raindrop_backup.py")

    yield temp_dir
    
    # No per-test teardown needed anymore

# --- Test Cases ---

def test_successful_single_task(test_environment):
    """
    Tests that a single, simple task runs successfully and creates output.
    """
    print("TEST: A simple successful task runs and creates output.")
    
    result = subprocess.run([
        sys.executable,
        str(ROOT_DIR / "src" / "master_automation.py"),
        "run-tasks",
        "export-personal"
    ], capture_output=True, text=True)
    
    assert result.returncode == 0, f"Script should have succeeded, but failed with stderr:\n{result.stderr}"
    output_dir = test_environment / "json"
    json_files = list(output_dir.glob("*-personal_*.json"))
    assert output_dir.exists(), "The 'json' output directory was not created."
    assert len(json_files) > 0, "The expected JSON backup file was not created."


def test_failure_stops_run(test_environment, monkeypatch):
    """
    Tests that if CONTINUE_ON_ERROR is false, the run aborts on the first failure.
    """
    print("TEST: A failure correctly stops the entire run.")
    
    # ARRANGE: Break the Raindrop script path for this test
    monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/raindrop_backup_broken.py")

    # ACT: Run the broken task first, then a working one.
    result = subprocess.run([
        sys.executable,
        str(ROOT_DIR / "src" / "master_automation.py"),
        "run-tasks",
        "raindrop-backup",
        "export-personal"
    ], capture_output=True, text=True)

    # ASSERT: Check that it failed and did NOT continue
    assert result.returncode == 1, "Script should have failed with exit code 1."
    output_dir = test_environment / "json"
    assert not output_dir.exists(), "Script continued after failure, but it should have stopped."


def test_failure_continues_on_error(test_environment, monkeypatch):
    """
    Tests that if CONTINUE_ON_ERROR is true, the run continues after a failure.
    """
    print("TEST: A failure correctly continues to the next task.")
    
    # ARRANGE: Break the Raindrop script path AND enable continue-on-error
    monkeypatch.setenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/raindrop_backup_broken.py")
    monkeypatch.setenv("CONTINUE_ON_ERROR", "true")

    # ACT: Run the broken task first, then a working one.
    result = subprocess.run([
        sys.executable,
        str(ROOT_DIR / "src" / "master_automation.py"),
        "run-tasks",
        "raindrop-backup",
        "export-personal"
    ], capture_output=True, text=True)

    # ASSERT: Check that it failed overall, but DID continue
    assert result.returncode == 1, "Script should have failed overall with exit code 1."
    output_dir = test_environment / "json"
    json_files = list(output_dir.glob("*-personal_*.json"))
    assert output_dir.exists(), "The 'json' output directory was not created, meaning the script did not continue."
    assert len(json_files) > 0, "The expected JSON file was not created, meaning the script did not continue."