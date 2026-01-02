import os, sys, subprocess, shutil, pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

@pytest.fixture
def env(monkeypatch):
    tmp = ROOT / "_tests" / "temp"
    if tmp.exists(): shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    
    monkeypatch.setenv("AUTOMATION_TEST_MODE", "true")
    monkeypatch.setenv("BW_VAULTS_DIR", str(tmp))
    # Mock Sources
    src_2fa = tmp / "src_2fa"; src_2fa.mkdir()
    src_bak = tmp / "src_bak"; src_bak.mkdir()
    monkeypatch.setenv("SYNC_2FA_SOURCE_DIR", str(src_2fa))
    monkeypatch.setenv("SYNC_BACKUPS_SOURCE_DIR", str(src_bak))
    # Mock Dests (Links)
    monkeypatch.setenv("SYNC_2FA_DEST", str(tmp / "2fa"))
    monkeypatch.setenv("SYNC_BACKUPS_DEST", str(tmp / "backups"))
    return tmp

def test_dry_run(env):
    res = subprocess.run([sys.executable, str(ROOT / "src/master_automation.py"), "run-tasks", "run-all", "--dry-run"], capture_output=True, text=True)
    assert res.returncode == 0