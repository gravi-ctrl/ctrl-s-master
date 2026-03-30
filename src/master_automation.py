#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import json
import time
import html
import shutil
import requests
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT_DIR / '.env')

# Paths
SRC_DIR = ROOT_DIR / 'src'
TOOLS_DIR = SRC_DIR / '_tools'
VAULTS_DIR = ROOT_DIR / 'vaults'
LOGS_DIR = ROOT_DIR / '_logs'

STATUS_FILE = Path(os.getenv("STATUS_FILE", str(ROOT_DIR / 'status.json')))
STATUS_DASHBOARD_FILE = Path(os.getenv("STATUS_DASHBOARD_FILE", str(ROOT_DIR / 'status_dashboard.md')))
FAILURE_LOG_FILE = LOGS_DIR / "failure_details.log"

# Config
BITWARDEN_PERSONAL_PASSWORD = os.getenv("BITWARDEN_PERSONAL_PASSWORD")
BITWARDEN_WORK_PASSWORD = os.getenv("BITWARDEN_WORK_PASSWORD")
KDBX_PERSONAL_PASSWORD = os.getenv("KDBX_PERSONAL_PASSWORD")
KDBX_WORK_PASSWORD = os.getenv("KDBX_WORK_PASSWORD")

BW_EXPORT_SCRIPT_PATH = ROOT_DIR / os.getenv("BW_EXPORT_SCRIPT_PATH", "src/_tools/bitwarden_exporter.py")
RAINDROP_BACKUP_SCRIPT_PATH = ROOT_DIR / os.getenv("RAINDROP_BACKUP_SCRIPT_PATH", "src/_tools/raindrop_backup.py")

# Sync Sources
SYNC_2FA_SOURCE = os.getenv("SYNC_2FA_SOURCE_DIR")
SYNC_BACKUPS_SOURCE = os.getenv("SYNC_BACKUPS_SOURCE_DIR")
# Destinations (Symlinks)
SYNC_2FA_DEST = ROOT_DIR / os.getenv("SYNC_2FA_DEST", "2fa")
SYNC_BACKUPS_DEST = ROOT_DIR / os.getenv("SYNC_BACKUPS_DEST", "backups")

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CONTINUE_ON_ERROR = os.getenv("CONTINUE_ON_ERROR", "false").lower() == "true"

# Safety Limits
CMD_TIMEOUT_SECONDS = 600  # Kill any task if it freezes for 10 minutes
REQUEST_TIMEOUT = 30       # Kill Telegram requests if they freeze for 30s

def _check_env_vars(required_vars: list[str]) -> bool:
    missing = [var for var in required_vars if not globals().get(var) and not os.getenv(var)]
    if missing:
        print(f"FATAL ERROR: Required variables missing from .env file: {', '.join(missing)}")
        return False
    return True

def run_command(command_list, is_python_script=False, working_dir=None):
    if is_python_script:
        python_bin = ROOT_DIR / "venv" / "bin" / "python3"
        full_cmd_list = [str(python_bin)] + [str(x) for x in command_list]
        print(f"--- Executing Python Script: {command_list[0].name} ---")
    else:
        full_cmd_list = [str(x) for x in command_list]
        print(f"--- Executing: {' '.join(full_cmd_list)} ---")

    env = os.environ.copy()
    env["AUTOMATION_ROOT"] = str(ROOT_DIR)

    try:
        # TIMEOUT & DEVNULL ADDED:
        # timeout=600: Ensures it doesn't hang forever.
        # stdin=subprocess.DEVNULL: Ensures it never waits for user input.
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
        
        error_msg = f"TIMEOUT EXPIRED: Process ran longer than {CMD_TIMEOUT_SECONDS} seconds.\n"
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

# --- Tasks ---

def export_personal(dry_run=False):
    print("\n" + "="*70); print("--- Task: Exporting Personal Vault ---")
    if not _check_env_vars(["BITWARDEN_PERSONAL_PASSWORD"]): return False, "Missing env vars"
    return run_command([BW_EXPORT_SCRIPT_PATH, 'personal'], is_python_script=True)

def export_work(dry_run=False):
    print("\n" + "="*70); print("--- Task: Exporting Work Vault ---")
    if not _check_env_vars(["BITWARDEN_WORK_PASSWORD"]): return False, "Missing env vars"
    return run_command([BW_EXPORT_SCRIPT_PATH, 'work'], is_python_script=True)

def convert_json_to_kdbx(dry_run=False):
    print("\n" + "="*70); print("--- Task: Converting New JSON to KDBX ---")
    if not _check_env_vars(["KDBX_PERSONAL_PASSWORD", "KDBX_WORK_PASSWORD"]): return False, "Missing env vars"
    converter_script = TOOLS_DIR / 'convert-to-kdbx.py'

    vaults_dir_override = os.getenv("DRY_RUN_VAULTS_DIR")
    json_dir = (Path(vaults_dir_override) if vaults_dir_override else VAULTS_DIR) / 'json'
    if not json_dir.exists(): return True, ""

    json_files = list(json_dir.glob('*.json'))
    all_success = True; final_output = ""

    for json_file in json_files:
        if 'personal' in json_file.name.lower(): os.environ["KDBX_PASSWORD_OVERRIDE"] = KDBX_PERSONAL_PASSWORD
        elif 'work' in json_file.name.lower(): os.environ["KDBX_PASSWORD_OVERRIDE"] = KDBX_WORK_PASSWORD
        else: continue

        success, output = run_command([converter_script, str(json_file)], is_python_script=True)
        os.environ.pop("KDBX_PASSWORD_OVERRIDE", None)
        if not success:
            all_success = False
            final_output += f"\n[File: {json_file.name}]\n{output}\n"

    return all_success, final_output if not all_success else ""

def raindrop_backup(dry_run=False):
    print("\n" + "="*70); print("--- Task: Backing up Raindrop.io ---")
    return run_command([RAINDROP_BACKUP_SCRIPT_PATH], is_python_script=True)

def run_rsync_sync(source, dest, task_name, dry_run=False, excludes=None):
    if not source or not dest: return False, f"Missing paths for {task_name}."
    if not Path(source).exists(): return False, f"Source directory does not exist: {source}"
    if not dry_run: Path(dest).mkdir(parents=True, exist_ok=True)

    print(f"Syncing FROM: {source}")
    print(f"Syncing TO:   {dest}")

    cmd = ["rsync", "-rltDv", "--delete"]
    if dry_run: cmd.append("--dry-run")

    if excludes:
        for item in excludes:
            cmd.append(f"--exclude={item}")

    cmd.append(str(source).rstrip('/') + '/')
    cmd.append(str(dest).rstrip('/') + '/')

    return run_command(cmd)

def sync_2fa(dry_run=False):
    print("\n" + "="*70); print("--- Task: Encrypting 2FA Files ---")
    return run_rsync_sync(SYNC_2FA_SOURCE, SYNC_2FA_DEST, "Sync 2FA", dry_run, excludes=['.stfolder', '.stversions'])

def sync_backups(dry_run=False):
    print("\n" + "="*70); print("--- Task: Encrypting General Backups ---")
    return run_rsync_sync(SYNC_BACKUPS_SOURCE, SYNC_BACKUPS_DEST, "Sync Backups", dry_run, excludes=['_pvt', '.stfolder', '.stversions'])

# --- Reporting ---

def send_telegram_report(job_succeeded, generate_log_only=False):
    if not generate_log_only: 
        print("\n" + "="*70)
        print("--- Task: Sending Telegram Report ---")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("SKIPPING REPORT: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return

    # 1. Prepare Message
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status_icon = "✅" if job_succeeded else "❌"
    
    # Simulating a Subject Line via Bold text
    message_text = f"<b>{status_icon} ctrl_s_master Automation Report</b>\n"
    message_text += f"<b>Status:</b> {'SUCCESS' if job_succeeded else 'FAILURE'}\n"
    message_text += f"<b>Time:</b> {timestamp}\n"

    # Check for specific failure details saved during run-tasks
    if not job_succeeded and FAILURE_LOG_FILE.exists():
        try:
            with open(FAILURE_LOG_FILE, 'r') as f:
                raw_error = f.read()
                # Truncate to avoid Telegram 4096 char limit
                if len(raw_error) > 3000:
                    raw_error = raw_error[:3000] + "\n...[TRUNCATED]..."
                
                # HTML Escape to prevent parsing errors
                clean_error = html.escape(raw_error)
                message_text += f"\n<b>⚠️ Error Details:</b>\n<pre>{clean_error}</pre>"
        except Exception as e:
            message_text += f"\n(Could not read failure log: {e})"

    # 2. Send Text Message
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

    # 3. Send Attachments (Dashboard & Logs)
    files_to_send = []
    
    # A. Dashboard (Always included if exists)
    if STATUS_DASHBOARD_FILE.exists(): 
        files_to_send.append(STATUS_DASHBOARD_FILE)
    
    # B. Logs (Dependent on .env configuration)
    log_patterns = os.getenv("EMAIL_ATTACH_LOGS", "")
    if log_patterns:
        for pat in log_patterns.split(','):
            logs = list(LOGS_DIR.glob(pat.strip()))
            if logs: 
                # Pick the most recent log for each pattern
                files_to_send.append(sorted(logs, key=os.path.getmtime, reverse=True)[0])

    if not generate_log_only and files_to_send:
        print(f"Sending {len(files_to_send)} attachments...")
        for file_path in files_to_send:
            try:
                with open(file_path, 'rb') as f:
                    # Telegram sendDocument
                    requests.post(
                        f"{base_url}/sendDocument", 
                        data={"chat_id": TELEGRAM_CHAT_ID}, 
                        files={"document": f},
                        timeout=REQUEST_TIMEOUT
                    )
            except Exception as e:
                print(f"Failed to send document {file_path.name}: {e}")

    # Cleanup failure log
    if FAILURE_LOG_FILE.exists():
        os.remove(FAILURE_LOG_FILE)
        
    print("Telegram report finished.")

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
        else: status_data = {"run_history": []}
    except: status_data = {"run_history": []}

    status_data['last_run_status'] = run_summary['run_status']
    if success: status_data['last_success_timestamp'] = run_summary['end_timestamp']
    else: status_data['last_failure_timestamp'] = run_summary['end_timestamp']

    status_data["run_history"] = [run_summary] + status_data.get("run_history", [])[:9]

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
    for run in status_data.get('run_history', []):
        start_raw = run.get('start_timestamp') or run.get('start')
        end_raw = run.get('end_timestamp') or run.get('end')
        start_str = datetime.fromisoformat(start_raw).strftime('%Y-%m-%d %H:%M:%S') if start_raw else "N/A"
        end_str = datetime.fromisoformat(end_raw).strftime('%Y-%m-%d %H:%M:%S') if end_raw else "N/A"
        status = run.get('run_status', 'UNKNOWN')
        s_icon = "✅" if status == "SUCCESS" else "❌"

        md_content.append(f"### Run from: {start_str} to {end_str} - {s_icon} {status}\n")
        md_content.append("| Task Name | Status | Duration (s) |")
        md_content.append("| :--- | :--- | :--- |")
        tasks = run.get('tasks_summary') or run.get('tasks') or {}
        for name, det in tasks.items():
            md_content.append(f"| {name} | {det.get('status')} | {det.get('duration')} |")
        md_content.append(f"\n**Total Duration:** {run.get('total_duration_seconds', 0)} seconds\n\n---\n")

    with open(STATUS_DASHBOARD_FILE, 'w', encoding='utf-8') as f: f.write("\n".join(md_content))

def main():
    all_tasks = {
        'raindrop-backup': raindrop_backup,
        'sync-backups': sync_backups,
        'sync-2fa': sync_2fa,
        'export-personal': export_personal,
        'export-work': export_work,
        'convert-kdbx': convert_json_to_kdbx
    }

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)
    run_parser = subparsers.add_parser('run-tasks')
    run_parser.add_argument('task_names', nargs='+'); run_parser.add_argument('--dry-run', action='store_true')
    report_parser = subparsers.add_parser('send-report')
    report_parser.add_argument('status', choices=['success', 'failure'])
    report_parser.add_argument('--generate-log-only', action='store_true')

    args = parser.parse_args()

    if args.command == 'run-tasks':
        # Clear old failure log if it exists from a previous run
        if FAILURE_LOG_FILE.exists():
            try: os.remove(FAILURE_LOG_FILE)
            except: pass

        if args.dry_run:
            dry_run_dir = ROOT_DIR / "_dry_run_output"
            if dry_run_dir.exists(): shutil.rmtree(dry_run_dir)
            dry_run_dir.mkdir()
            os.environ["DRY_RUN_VAULTS_DIR"] = str(dry_run_dir)
            os.environ["SYNC_2FA_DEST"] = str(dry_run_dir / "2fa")
            os.environ["SYNC_BACKUPS_DEST"] = str(dry_run_dir / "backups")

        start = datetime.now()
        task_list = list(all_tasks.values()) if 'run-all' in args.task_names else [all_tasks[n] for n in args.task_names if n in all_tasks]

        overall_success = True
        task_results = {}
        failed_task = None
        failure_output = None

        for task_func in task_list:
            print(f"\n### Starting Task: {task_func.__name__}")
            t_start = time.time()
            success, output = task_func(dry_run=args.dry_run)
            task_results[task_func.__name__] = {"status": "SUCCESS" if success else "FAILURE", "duration": round(time.time()-t_start, 2)}

            if not success:
                overall_success = False
                failed_task = task_func.__name__
                failure_output = output
                
                # SAVE FAILURE LOG: This allows the reporting step to see what went wrong
                try:
                    with open(FAILURE_LOG_FILE, 'w') as f:
                        f.write(f"Task: {failed_task}\n\n{failure_output}")
                except Exception as e:
                    print(f"Could not write failure log: {e}")

                if not CONTINUE_ON_ERROR: break

        update_status_json(start, datetime.now(), overall_success, task_results)
        update_status_dashboard_md()

        if args.dry_run and dry_run_dir.exists(): shutil.rmtree(dry_run_dir)
        sys.exit(0 if overall_success else 1)

    elif args.command == 'send-report':
        send_telegram_report(args.status == 'success', generate_log_only=args.generate_log_only)

if __name__ == "__main__":
    main()
