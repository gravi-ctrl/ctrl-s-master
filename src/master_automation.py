#!/usr/bin/env python3
import os
import sys
import platform
import subprocess
import argparse
import json
import time
import html
import shutil
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# ── OS Detection ──────────────────────────────────────────────────────────────
IS_WINDOWS = platform.system() == "Windows"

ROOT_DIR = Path(__file__).resolve().parents[1]

# Load .env from disk only when the file is actually present.
_env_file = ROOT_DIR / '.env'
if _env_file.exists():
    load_dotenv(dotenv_path=_env_file, override=False)

# Expose AUTOMATION_ROOT globally so os.path.expandvars can use it in Linux configs
os.environ["AUTOMATION_ROOT"] = str(ROOT_DIR)

# ── Paths ─────────────────────────────────────────────────────────────────────
SRC_DIR    = ROOT_DIR / 'src'
TOOLS_DIR  = SRC_DIR / '_tools'
VAULTS_DIR = ROOT_DIR / 'vaults'
LOGS_DIR   = ROOT_DIR / '_logs'

STATUS_FILE           = Path(os.getenv("STATUS_FILE",           str(ROOT_DIR / 'status.json')))
STATUS_DASHBOARD_FILE = Path(os.getenv("STATUS_DASHBOARD_FILE", str(ROOT_DIR / 'status_dashboard.md')))
FAILURE_LOG_FILE      = LOGS_DIR / "failure_details.log"

# ── Config ────────────────────────────────────────────────────────────────────
KDBX_PERSONAL_PASSWORD      = os.getenv("KDBX_PERSONAL_PASSWORD")
KDBX_WORK_PASSWORD          = os.getenv("KDBX_WORK_PASSWORD")

BW_EXPORT_SCRIPT_PATH       = ROOT_DIR / os.getenv("BW_EXPORT_SCRIPT_PATH",       "src/_tools/bitwarden_exporter.py")
RAINDROP_BACKUP_SCRIPT_PATH = ROOT_DIR / os.getenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/raindrop_backup.py")

# ── Sync — Windows (FreeFileSync, auto-discovered) ────────────────────────────
_ffs_raw = os.getenv("FFS_PATH", "")
FFS_PATH = (ROOT_DIR / _ffs_raw) if _ffs_raw and not Path(_ffs_raw).is_absolute() else Path(_ffs_raw)
FFS_JOBS_DIR = TOOLS_DIR / 'ffs_jobs'

# ── Sync — Linux (rsync, auto-discovered) ─────────────────────────────────────
RSYNC_JOBS_DIR = TOOLS_DIR / 'rsync_jobs'

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── Email ─────────────────────────────────────────────────────────────────────
EMAIL_HOST      = os.getenv("EMAIL_HOST",     "smtp.gmail.com")
EMAIL_PORT      = int(os.getenv("EMAIL_PORT", "465"))
EMAIL_SENDER    = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD  = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")

# ── Behaviour ─────────────────────────────────────────────────────────────────
CONTINUE_ON_ERROR   = os.getenv("CONTINUE_ON_ERROR", "false").lower() == "true"
CMD_TIMEOUT_SECONDS = 600
REQUEST_TIMEOUT     = 30

# ── venv-aware Python binary ──────────────────────────────────────────────────
if IS_WINDOWS:
    _VENV_PYTHON = ROOT_DIR / "venv" / "Scripts" / "python.exe"
else:
    _VENV_PYTHON = ROOT_DIR / "venv" / "bin" / "python3"


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _check_env_vars(required_vars: list[str]) -> bool:
    missing =[v for v in required_vars if not globals().get(v) and not os.getenv(v)]
    if missing:
        print(f"FATAL ERROR: Required variables missing from .env file: {', '.join(missing)}")
        return False
    return True


def run_command(command_list, is_python_script=False, working_dir=None):
    if is_python_script:
        full_cmd_list =[str(_VENV_PYTHON)] +[str(x) for x in command_list]
        print(f"--- Executing Python Script: {Path(command_list[0]).name} ---")
    else:
        full_cmd_list =[str(x) for x in command_list]
        print(f"--- Executing: {' '.join(full_cmd_list)} ---")

    env = os.environ.copy()

    try:
        process = subprocess.run(
            full_cmd_list, check=True, capture_output=True,
            env=env, text=True, encoding='utf-8', errors='replace',
            cwd=working_dir or ROOT_DIR,
            timeout=CMD_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL
        )
        if process.stdout: print(process.stdout.strip())
        if process.stderr: print(process.stderr.strip())
        print("--- Command finished successfully. ---")
        return True, ""

    except subprocess.TimeoutExpired as e:
        print(f"--- !!! COMMAND TIMED OUT ({CMD_TIMEOUT_SECONDS}s) !!! ---")
        stdout_capture = e.stdout.decode('utf-8', errors='replace') if e.stdout else "No STDOUT"
        stderr_capture = e.stderr.decode('utf-8', errors='replace') if e.stderr else "No STDERR"
        error_msg  = f"TIMEOUT EXPIRED: Process ran longer than {CMD_TIMEOUT_SECONDS} seconds.\n"
        error_msg += f"\n--- PARTIAL STDOUT ---\n{stdout_capture}\n"
        error_msg += f"\n--- PARTIAL STDERR ---\n{stderr_capture}\n"
        return False, error_msg

    except subprocess.CalledProcessError as e:
        print("--- !!! COMMAND FAILED !!! ---")
        print(f"Return code: {e.returncode}")
        error_msg = f"EXIT CODE: {e.returncode}\n"
        if e.stdout:
            print(f"STDOUT:\n{e.stdout.strip()}")
            error_msg += f"\n--- STDOUT ---\n{e.stdout.strip()}\n"
        if e.stderr:
            print(f"STDERR:\n{e.stderr.strip()}")
            error_msg += f"\n--- STDERR ---\n{e.stderr.strip()}\n"
        return False, error_msg


# ─────────────────────────────────────────────────────────────────────────────
#  Tasks — Bitwarden / Raindrop
# ─────────────────────────────────────────────────────────────────────────────

def raindrop_personal(dry_run=False):
    print("\n" + "="*70); print("--- Task: Backing up Raindrop.io (Personal) ---")
    if not os.getenv("RAINDROP_PERSONAL_API_TOKEN"):
        print("SKIP: Raindrop Personal not configured in .env")
        return "SKIPPED", ""
    if not os.getenv("RAINDROP_BACKUP_DESTINATION"):
        return "FAILURE", "Missing RAINDROP_BACKUP_DESTINATION in .env"
    success, out = run_command([RAINDROP_BACKUP_SCRIPT_PATH, 'personal'], is_python_script=True)
    return "SUCCESS" if success else "FAILURE", out

def raindrop_work(dry_run=False):
    print("\n" + "="*70); print("--- Task: Backing up Raindrop.io (Work) ---")
    if not os.getenv("RAINDROP_WORK_API_TOKEN"):
        print("SKIP: Raindrop Work not configured in .env")
        return "SKIPPED", ""
    if not os.getenv("RAINDROP_BACKUP_DESTINATION"):
        return "FAILURE", "Missing RAINDROP_BACKUP_DESTINATION in .env"
    success, out = run_command([RAINDROP_BACKUP_SCRIPT_PATH, 'work'], is_python_script=True)
    return "SUCCESS" if success else "FAILURE", out

def export_personal(dry_run=False):
    print("\n" + "="*70); print("--- Task: Exporting Personal Vault ---")
    if not os.getenv("BW_PERSONAL_CLIENT_ID_UUID"):
        print("SKIP: Personal Vault not configured in .env")
        return "SKIPPED", ""
    if not _check_env_vars(["BITWARDEN_PERSONAL_PASSWORD"]):
        return "FAILURE", "Missing BITWARDEN_PERSONAL_PASSWORD"
    success, out = run_command([BW_EXPORT_SCRIPT_PATH, 'personal'], is_python_script=True)
    return "SUCCESS" if success else "FAILURE", out

def export_work(dry_run=False):
    print("\n" + "="*70); print("--- Task: Exporting Work Vault ---")
    if not os.getenv("BW_WORK_CLIENT_ID_UUID"):
        print("SKIP: Work Vault not configured in .env")
        return "SKIPPED", ""
    if not _check_env_vars(["BITWARDEN_WORK_PASSWORD"]):
        return "FAILURE", "Missing BITWARDEN_WORK_PASSWORD"
    success, out = run_command([BW_EXPORT_SCRIPT_PATH, 'work'], is_python_script=True)
    return "SUCCESS" if success else "FAILURE", out

def convert_json_to_kdbx(dry_run=False):
    print("\n" + "="*70); print("--- Task: Converting New JSON to KDBX ---")
    converter_script = TOOLS_DIR / 'convert-to-kdbx.py'

    vaults_dir_override = os.getenv("DRY_RUN_VAULTS_DIR")
    json_dir = (Path(vaults_dir_override) if vaults_dir_override else VAULTS_DIR) / 'json'
    
    if not json_dir.exists(): 
        print("SKIP: JSON directory does not exist. Nothing to convert.")
        return "SKIPPED", ""

    json_files = list(json_dir.glob('*.json'))
    if not json_files: 
        print("SKIP: No JSON files found. Nothing to convert.")
        return "SKIPPED", ""

    all_success = True; final_output = ""

    for json_file in json_files:
        if 'personal' in json_file.name.lower():
            pwd = KDBX_PERSONAL_PASSWORD
            if not pwd:
                all_success = False
                final_output += f"\n[File: {json_file.name}]\nMissing KDBX_PERSONAL_PASSWORD in .env\n"
                continue
            os.environ["KDBX_PASSWORD_OVERRIDE"] = pwd
            
        elif 'work' in json_file.name.lower():
            pwd = KDBX_WORK_PASSWORD
            if not pwd:
                all_success = False
                final_output += f"\n[File: {json_file.name}]\nMissing KDBX_WORK_PASSWORD in .env\n"
                continue
            os.environ["KDBX_PASSWORD_OVERRIDE"] = pwd
        else:
            continue

        success, output = run_command([converter_script, str(json_file)], is_python_script=True)
        os.environ.pop("KDBX_PASSWORD_OVERRIDE", None)
        if not success:
            all_success = False
            final_output += f"\n[File: {json_file.name}]\n{output}\n"

    return "SUCCESS" if all_success else "FAILURE", final_output if not all_success else ""


# ─────────────────────────────────────────────────────────────────────────────
#  Tasks — Sync: Linux (rsync, auto-discovered)
# ─────────────────────────────────────────────────────────────────────────────

def run_rsync_sync(source, dest, task_name, dry_run=False, excludes=None):
    if not source or not dest: return False, f"Missing paths for {task_name}."
    if not Path(source).exists(): return False, f"Source directory does not exist: {source}"

    if not dry_run:
        dest_path = Path(dest).resolve()
        dest_path.mkdir(parents=True, exist_ok=True)

    print(f"Syncing FROM: {source}")
    print(f"Syncing TO:   {dest}")

    cmd =["rsync", "-rltDv", "--delete"]
    if dry_run: cmd.append("--dry-run")
    if excludes:
        for item in excludes:
            cmd.append(f"--exclude={item}")
    cmd.append(str(source).rstrip('/') + '/')
    cmd.append(str(dest).rstrip('/') + '/')
    return run_command(cmd)

def _make_rsync_task(job_file: Path):
    def rsync_task(dry_run=False):
        print("\n" + "="*70)
        print(f"--- Task: Rsync Job[{job_file.stem}] ---")
        
        try:
            with open(job_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            error_msg = f"Failed to parse JSON config in {job_file.name}: {e}"
            print(f"❌ {error_msg}")
            return "FAILURE", error_msg

        raw_source = config.get("source", "")
        raw_dest   = config.get("dest", "")
        
        source = os.path.expandvars(raw_source)
        dest   = os.path.expandvars(raw_dest)
        excludes = config.get("excludes",[])

        if not source or (source == raw_source and raw_source.startswith('$')):
            return "FAILURE", f"Source path missing or environment variable unresolved: {raw_source}"
        if not dest or (dest == raw_dest and raw_dest.startswith('$')):
            return "FAILURE", f"Dest path missing or environment variable unresolved: {raw_dest}"

        success, out = run_rsync_sync(source, dest, job_file.stem, dry_run=dry_run, excludes=excludes)
        return "SUCCESS" if success else "FAILURE", out

    rsync_task.__name__ = f"rsync_{job_file.stem}"
    return rsync_task

def _discover_rsync_tasks() -> dict:
    if not RSYNC_JOBS_DIR.exists():
        print(f"[INFO] Rsync jobs directory not found: {RSYNC_JOBS_DIR}")
        print( "       Create it and drop *.json config files inside to add sync jobs.")
        return {}

    json_files = sorted(RSYNC_JOBS_DIR.glob("*.json"))
    if not json_files:
        print(f"[INFO] No *.json files found in {RSYNC_JOBS_DIR}")
        return {}

    tasks = {}
    for job_path in json_files:
        task_key = f"rsync-{job_path.stem}"
        tasks[task_key] = _make_rsync_task(job_path)
        print(f"[INFO] Registered Rsync job: {task_key} -> {job_path.name}")

    return tasks


# ─────────────────────────────────────────────────────────────────────────────
#  Tasks — Sync: Windows (FreeFileSync, auto-discovered)
# ─────────────────────────────────────────────────────────────────────────────

def _make_ffs_task(batch_path: Path):
    def ffs_task(dry_run=False):
        print("\n" + "="*70)
        print(f"--- Task: FFS Sync [{batch_path.stem}] ---")
        print(f"Batch file: {batch_path}")

        if dry_run:
            print(f"DRY RUN: Would execute FFS batch job: {batch_path.name}")
            return "SUCCESS", ""

        if not str(FFS_PATH).strip() or not FFS_PATH.exists():
            error_msg = f"FreeFileSync executable not found at: '{FFS_PATH}'\nSet FFS_PATH in your .env to the correct path."
            print(f"❌ {error_msg}")
            return "FAILURE", error_msg

        success, out = run_command([FFS_PATH, str(batch_path)])
        return "SUCCESS" if success else "FAILURE", out

    ffs_task.__name__ = f"ffs_{batch_path.stem}"
    return ffs_task


def _discover_ffs_tasks() -> dict:
    if not FFS_JOBS_DIR.exists():
        print(f"[INFO] FFS jobs directory not found: {FFS_JOBS_DIR}")
        print( "       Create it and drop *.ffs_batch files inside to add sync jobs.")
        return {}

    batch_files = sorted(FFS_JOBS_DIR.glob("*.ffs_batch"))
    if not batch_files:
        print(f"[INFO] No *.ffs_batch files found in {FFS_JOBS_DIR}")
        return {}

    tasks = {}
    for batch_path in batch_files:
        task_key = f"ffs-{batch_path.stem}"
        tasks[task_key] = _make_ffs_task(batch_path)
        print(f"[INFO] Registered FFS sync job: {task_key} -> {batch_path.name}")

    return tasks


# ─────────────────────────────────────────────────────────────────────────────
#  Reporting — Telegram
# ─────────────────────────────────────────────────────────────────────────────

def _build_report_text(job_succeeded: bool) -> str:
    status_icon = "✅" if job_succeeded else "❌"
    status_text = "SUCCESS" if job_succeeded else "FAILURE"
    message_text = f"{status_icon} <b>Automation Run: {status_text}</b>\n"
    message_text += f"<i>Host: {platform.node()} | OS: {platform.system()}</i>\n"

    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, 'r') as f:
                status_data = json.load(f)
            last_run = status_data.get("run_history", [{}])[0]
            message_text += f"\n<b>Duration:</b> {last_run.get('total_duration_seconds', 'N/A')}s"
            tasks = last_run.get("tasks_summary", {})
            if tasks:
                message_text += "\n\n<b>Tasks:</b>"
                for name, det in tasks.items():
                    t_icon = "✅" if det.get("status") == "SUCCESS" else "❌"
                    message_text += f"\n  {t_icon} {name} ({det.get('duration', '?')}s)"
        except Exception as e:
            message_text += f"\n(Could not read status file: {e})"

    if not job_succeeded and FAILURE_LOG_FILE.exists():
        try:
            with open(FAILURE_LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
                raw_error = f.read(1500)
            clean_error = html.escape(raw_error)
            message_text += f"\n<b>⚠️ Error Details:</b>\n<pre>{clean_error}</pre>"
        except Exception as e:
            message_text += f"\n(Could not read failure log: {e})"

    return message_text


def send_telegram_report(job_succeeded: bool, generate_log_only=False):
    if not generate_log_only:
        print("\n" + "="*70)
        print("--- Task: Sending Telegram Report ---")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing). Skipping.")
        return

    message_text = _build_report_text(job_succeeded)
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    if not generate_log_only:
        try:
            r = requests.post(f"{base_url}/sendMessage", data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message_text,
                "parse_mode": "HTML"
            }, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                print(f"Failed to send Telegram text: {r.text}")
        except Exception as e:
            print(f"Telegram Connection Error: {e}")

    files_to_send =[]
    if STATUS_DASHBOARD_FILE.exists():
        files_to_send.append(STATUS_DASHBOARD_FILE)
    log_patterns = os.getenv("EMAIL_ATTACH_LOGS", "")
    if log_patterns:
        for pat in log_patterns.split(','):
            logs = list(LOGS_DIR.glob(pat.strip()))
            if logs:
                files_to_send.append(sorted(logs, key=os.path.getmtime, reverse=True)[0])

    if not generate_log_only and files_to_send:
        print(f"Sending {len(files_to_send)} attachments via Telegram...")
        for file_path in files_to_send:
            try:
                with open(file_path, 'rb') as f:
                    requests.post(
                        f"{base_url}/sendDocument",
                        data={"chat_id": TELEGRAM_CHAT_ID},
                        files={"document": f},
                        timeout=REQUEST_TIMEOUT
                    )
            except Exception as e:
                print(f"Failed to send document {file_path.name}: {e}")

    if FAILURE_LOG_FILE.exists():
        os.remove(FAILURE_LOG_FILE)
    print("Telegram report finished.")


# ─────────────────────────────────────────────────────────────────────────────
#  Reporting — Email
# ─────────────────────────────────────────────────────────────────────────────

def send_email_report(job_succeeded: bool, generate_log_only=False):
    if not generate_log_only:
        print("\n" + "="*70)
        print("--- Task: Sending Email Report ---")

    if not all([EMAIL_HOST, EMAIL_PORT, EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("Email not configured (EMAIL_HOST/PORT/SENDER/PASSWORD/RECIPIENT missing). Skipping.")
        return

    status_text = "SUCCESS ✅" if job_succeeded else "FAILURE ❌"
    subject = f"[Automation] Run {status_text} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    raw_report = _build_report_text(job_succeeded)
    import re
    plain_body = re.sub(r'<[^>]+>', '', raw_report).strip()

    msg = MIMEMultipart()
    msg['From']    = EMAIL_SENDER
    msg['To']      = EMAIL_RECIPIENT
    msg['Subject'] = subject
    msg.attach(MIMEText(plain_body, 'plain', 'utf-8'))

    files_to_attach =[]
    if STATUS_DASHBOARD_FILE.exists():
        files_to_attach.append(STATUS_DASHBOARD_FILE)
    log_patterns = os.getenv("EMAIL_ATTACH_LOGS", "")
    if log_patterns:
        for pat in log_patterns.split(','):
            logs = list(LOGS_DIR.glob(pat.strip()))
            if logs:
                files_to_attach.append(sorted(logs, key=os.path.getmtime, reverse=True)[0])

    for file_path in files_to_attach:
        try:
            with open(file_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{file_path.name}"')
            msg.attach(part)
        except Exception as e:
            print(f"Could not attach {file_path.name}: {e}")

    if generate_log_only:
        print(f"[LOG ONLY] Email report prepared for: {EMAIL_RECIPIENT}")
        return

    try:
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        print(f"Email report sent to {EMAIL_RECIPIENT}.")
    except Exception as e:
        print(f"Email send error: {e}")

    if FAILURE_LOG_FILE.exists():
        os.remove(FAILURE_LOG_FILE)
    print("Email report finished.")


# ─────────────────────────────────────────────────────────────────────────────
#  Reporting — dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def send_report(job_succeeded: bool, generate_log_only=False):
    sent_anything = False

    if all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        send_email_report(job_succeeded, generate_log_only)
        sent_anything = True

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        send_telegram_report(job_succeeded, generate_log_only)
        sent_anything = True

    if not sent_anything:
        preferred = "EMAIL_SENDER/EMAIL_PASSWORD/EMAIL_RECIPIENT" if IS_WINDOWS else "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"
        print(f"WARNING: No notification channel configured. Set {preferred} in your .env file.")


# ─────────────────────────────────────────────────────────────────────────────
#  Status tracking
# ─────────────────────────────────────────────────────────────────────────────

def update_status_json(start, end, success, task_results):
    run_summary = {
        "run_status": "SUCCESS" if success else "FAILURE",
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
        "total_duration_seconds": round((end - start).total_seconds(), 2),
        "tasks_summary": task_results
    }
    try:
        if STATUS_FILE.exists():
            with open(STATUS_FILE, 'r') as f: status_data = json.load(f)
        else: status_data = {"run_history":[]}
    except: status_data = {"run_history": []}

    status_data['last_run_status'] = run_summary['run_status']
    if success: status_data['last_success_timestamp'] = run_summary['end_timestamp']
    else:       status_data['last_failure_timestamp'] = run_summary['end_timestamp']

    status_data["run_history"] =[run_summary] + status_data.get("run_history", [])[:9]
    with open(STATUS_FILE, 'w') as f: json.dump(status_data, f, indent=4)


def update_status_dashboard_md():
    if not STATUS_FILE.exists(): return
    try:
        with open(STATUS_FILE, 'r') as f: status_data = json.load(f)
    except: return

    md_content = ["# Automation Status Dashboard\n"]
    last_status = status_data.get('last_run_status', 'N/A')
    icon = "✅" if last_status == "SUCCESS" else "❌"
    md_content.append(f"- **Last Run Status:** {icon} {last_status}")

    ts = status_data.get('last_success_timestamp')
    md_content.append(f"- **Last Successful Run:** {datetime.fromisoformat(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else 'N/A'}")

    md_content.append("\n---\n## Run History\n")
    for run in status_data.get('run_history',[]):
        start_raw = run.get('start_timestamp') or run.get('start')
        end_raw   = run.get('end_timestamp')   or run.get('end')
        start_str = datetime.fromisoformat(start_raw).strftime('%Y-%m-%d %H:%M:%S') if start_raw else "N/A"
        end_str   = datetime.fromisoformat(end_raw).strftime('%Y-%m-%d %H:%M:%S')   if end_raw   else "N/A"
        status    = run.get('run_status', 'UNKNOWN')
        s_icon    = "✅" if status == "SUCCESS" else "❌"

        md_content.append(f"### Run from: {start_str} to {end_str} - {s_icon} {status}\n")
        md_content.append("| Task Name | Status | Duration (s) |")
        md_content.append("| :--- | :--- | :--- |")
        tasks = run.get('tasks_summary') or run.get('tasks') or {}
        for name, det in tasks.items():
            md_content.append(f"| {name} | {det.get('status')} | {det.get('duration')} |")
        md_content.append(f"\n**Total Duration:** {run.get('total_duration_seconds', 0)} seconds\n\n---\n")

    with open(STATUS_DASHBOARD_FILE, 'w', encoding='utf-8') as f: f.write("\n".join(md_content))


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # ── Base tasks: identical on both platforms ───────────────────────────────
    all_tasks = {
        'raindrop-personal': raindrop_personal,
        'raindrop-work':     raindrop_work,
        'export-personal':   export_personal,
        'export-work':       export_work,
        'convert-kdbx':      convert_json_to_kdbx,
    }

    # ── Sync tasks: platform-specific discovery ───────────────────────────────
    if IS_WINDOWS:
        all_tasks.update(_discover_ffs_tasks())
    else:
        all_tasks.update(_discover_rsync_tasks())

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)

    run_parser = subparsers.add_parser('run-tasks')
    run_parser.add_argument('task_names', nargs='+')
    run_parser.add_argument('--dry-run', action='store_true')

    report_parser = subparsers.add_parser('send-report')
    report_parser.add_argument('status', choices=['success', 'failure'])
    report_parser.add_argument('--generate-log-only', action='store_true')

    args = parser.parse_args()

    if args.command == 'run-tasks':
        # Clear previous failure log at start of run
        if FAILURE_LOG_FILE.exists():
            try: os.remove(FAILURE_LOG_FILE)
            except: pass

        if args.dry_run:
            dry_run_dir = ROOT_DIR / "_dry_run_output"
            if dry_run_dir.exists(): shutil.rmtree(dry_run_dir)
            dry_run_dir.mkdir()
            os.environ["DRY_RUN_VAULTS_DIR"] = str(dry_run_dir)

        start = datetime.now()
        task_list = (
            list(all_tasks.values()) if 'run-all' in args.task_names
            else [all_tasks[n] for n in args.task_names if n in all_tasks]
        )

        overall_success = True
        task_results    = {}

        for task_func in task_list:
            print(f"\n### Starting Task: {task_func.__name__}")
            t_start = time.time()
            
            # Tasks now return strings: "SUCCESS", "FAILURE", or "SKIPPED"
            status, output = task_func(dry_run=args.dry_run)
            
            # If the task is skipped, do NOT add it to the dashboard report
            if status == "SKIPPED":
                print(f"--- Task '{task_func.__name__}' Skipped ---")
                continue

            task_results[task_func.__name__] = {
                "status":   status,
                "duration": round(time.time() - t_start, 2)
            }

            if status == "FAILURE":
                overall_success = False
                failed_task    = task_func.__name__
                failure_output = output
                try:
                    with open(FAILURE_LOG_FILE, 'a', encoding='utf-8') as f:
                        f.write(f"Task: {failed_task}\n{failure_output}\n{'-'*40}\n")
                except Exception as e:
                    print(f"Could not write failure log: {e}")
                if not CONTINUE_ON_ERROR: break

        update_status_json(start, datetime.now(), overall_success, task_results)
        update_status_dashboard_md()

        if args.dry_run and (ROOT_DIR / "_dry_run_output").exists():
            shutil.rmtree(ROOT_DIR / "_dry_run_output")

        sys.exit(0 if overall_success else 1)

    elif args.command == 'send-report':
        send_report(args.status == 'success', generate_log_only=args.generate_log_only)


if __name__ == "__main__":
    main()