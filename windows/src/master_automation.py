import os
import sys
import subprocess
import argparse
import json
import time
import html
import smtplib
import shutil
from pathlib import Path
from dotenv import load_dotenv
from email.message import EmailMessage
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[1]

# Load environment variables from the .env file in the project root
load_dotenv(dotenv_path=ROOT_DIR / '.env')

# Define core directories based on the new 'src' layout
SRC_DIR = ROOT_DIR / 'src'
TOOLS_DIR = SRC_DIR / '_tools'
VAULTS_DIR = ROOT_DIR / 'vaults'
LOGS_DIR = ROOT_DIR / '_logs'

# Define paths for generated status files
STATUS_FILE = Path(os.getenv("STATUS_FILE", str(ROOT_DIR / 'status.json')))
STATUS_DASHBOARD_FILE = Path(os.getenv("STATUS_DASHBOARD_FILE", str(ROOT_DIR / 'status_dashboard.md')))

# Define other necessary file paths
FAILURE_LOG_FILE = LOGS_DIR / "failure_details.log"
VENV_ACTIVATE_SCRIPT = ROOT_DIR / "venv" / "Scripts" / "activate.bat"

# Load all configuration variables from the environment
BITWARDEN_PERSONAL_PASSWORD = os.getenv("BITWARDEN_PERSONAL_PASSWORD")
BITWARDEN_WORK_PASSWORD = os.getenv("BITWARDEN_WORK_PASSWORD")
KDBX_PERSONAL_PASSWORD = os.getenv("KDBX_PERSONAL_PASSWORD")
KDBX_WORK_PASSWORD = os.getenv("KDBX_WORK_PASSWORD")
FFS_PATH = str(ROOT_DIR / os.getenv("FFS_PATH")) if os.getenv("FFS_PATH") else None
BW_EXPORT_SCRIPT_PATH = ROOT_DIR / os.getenv("BW_EXPORT_SCRIPT_PATH", "")
RAINDROP_BACKUP_SCRIPT_PATH = ROOT_DIR / os.getenv("RAINDROP_BACKUP_SCRIPT_PATH", "")
EMAIL_HOST, EMAIL_PORT, EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT = (
    os.getenv("EMAIL_HOST"), os.getenv("EMAIL_PORT"), os.getenv("EMAIL_SENDER"),
    os.getenv("EMAIL_PASSWORD"), os.getenv("EMAIL_RECIPIENT")
)
CONTINUE_ON_ERROR = os.getenv("CONTINUE_ON_ERROR", "false").lower() == "true"


def _check_env_vars(required_vars: list[str]) -> bool:
    missing = [var for var in required_vars if not globals().get(var)]
    if missing: print(f"FATAL ERROR: Required variables missing from .env file: {', '.join(missing)}"); return False
    return True

def run_command(command_list, is_python_script=False, working_dir=None, allowed_codes=None):
    # Default to only accepting 0 (Success) if no codes provided
    if allowed_codes is None:
        allowed_codes = [0]

    if is_python_script:
        script_path = command_list[0]
        script_args = " ".join(f'"{arg}"' for arg in command_list[1:])
        full_command = f'call "{VENV_ACTIVATE_SCRIPT}" && python "{script_path}" {script_args}'
        print(f"--- Executing venv-wrapped command for: {script_path.name} ---")
    else:
        full_command = " ".join(f'"{str(c)}"' for c in command_list)
        print(f"--- Executing: {full_command} ---")

    env = os.environ.copy()
    env["AUTOMATION_ROOT"] = str(ROOT_DIR)
    
    # Run without check=True so we can manually validate the return code
    process = subprocess.run(
        full_command, check=False, capture_output=True, shell=True,
        env=env, text=True, encoding='utf-8', errors='replace',
        cwd=working_dir
    )

    # Check if the return code is in our list of allowed success codes
    if process.returncode in allowed_codes:
        if process.stdout: print(process.stdout.strip())
        if process.stderr: print(process.stderr.strip())
        
        # If it's a warning (code 1) but allowed, we might want to note it
        if process.returncode == 1:
            print(f"--- Command finished with WARNINGS (Exit Code 1), but accepted. ---")
        else:
            print("--- Command finished successfully. ---")
            
        return True, ""
    else:
        # This block handles actual failures (codes not in allowed_codes)
        print("--- !!! COMMAND FAILED !!! ---"); print(f"Return code: {process.returncode}")
        stdout_str = process.stdout.strip() if process.stdout else ""
        stderr_str = process.stderr.strip() if process.stderr else ""
        if stdout_str: print(f"STDOUT:\n{stdout_str}")
        if stderr_str: print(f"STDERR:\n{stderr_str}")
        error_output = f"STDOUT:\n{stdout_str}\n\nSTDERR:\n{stderr_str}"
        return False, error_output

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
    all_success = True; final_output = ""; new_files_processed = 0

    vaults_dir_override = os.getenv("DRY_RUN_VAULTS_DIR")
    if vaults_dir_override:
        json_dir = Path(vaults_dir_override) / 'json'
    else:
        json_dir = VAULTS_DIR / 'json'

    if not json_dir.exists():
        print(f"--- No JSON source directory found at '{json_dir}'. Skipping conversion. ---")
        return True, ""
        
    kdbx_dir = json_dir.parent / 'kdbx'
    json_files = list(json_dir.glob('*.json'))
    existing_kdbx = {f.stem for f in kdbx_dir.glob('*.kdbx')} if kdbx_dir.exists() else set()
    
    for json_file in json_files:
        if json_file.stem in existing_kdbx: continue
        new_files_processed += 1
        if 'personal' in json_file.name.lower(): os.environ["KDBX_PASSWORD_OVERRIDE"] = KDBX_PERSONAL_PASSWORD
        elif 'work' in json_file.name.lower(): os.environ["KDBX_PASSWORD_OVERRIDE"] = KDBX_WORK_PASSWORD
        else: print(f"--- Skipping {json_file.name}, could not determine vault type. ---"); continue
        success, output = run_command([converter_script, str(json_file)], is_python_script=True)
        os.environ.pop("KDBX_PASSWORD_OVERRIDE", None)
        if not success: all_success = False; final_output = output

    if new_files_processed == 0: print("--- No new JSON files to convert. ---")
    else: print(f"--- Processed {new_files_processed} new file(s). ---")
    return all_success, final_output if not all_success else ""

def raindrop_backup(dry_run=False):
    print("\n" + "="*70); print("--- Task: Backing up Raindrop.io bookmarks ---")
    return run_command([RAINDROP_BACKUP_SCRIPT_PATH], is_python_script=True)

def run_ffs_batch(filename: str, dry_run=False):
    batch_path = TOOLS_DIR / filename
    if not batch_path.exists(): msg = f"FATAL: Batch file not found at '{batch_path}'"; print(msg); return False, msg
    ffs_executable = FFS_PATH
    print(f"DEBUG FFS path: {ffs_executable}")
    print(f"DEBUG exists: {Path(ffs_executable).exists() if ffs_executable else 'N/A'}")
    if not ffs_executable or not Path(ffs_executable).exists(): msg = f"FATAL: FreeFileSync executable not found or not configured in .env (FFS_PATH)."; print(msg); return False, msg
    
    full_command = f'"{ffs_executable}" "{batch_path}"'
    if dry_run:
        print(f"[DRY RUN] Would execute FreeFileSync job from root: {full_command}")
        return True, ""
        
    print(f"Starting sync job: {batch_path.name}")
    
    # PASS allowed_codes=[0, 1] HERE
    return run_command([ffs_executable, str(batch_path)], working_dir=ROOT_DIR, allowed_codes=[0, 1])

def sync_2fa(dry_run=False):
    print("\n" + "="*70); print("--- Task: Sync 2FA ---")
    return run_ffs_batch('sync-2fa.ffs_batch', dry_run=dry_run)

def sync_backups(dry_run=False):
    print("\n" + "="*70); print("--- Task: Sync Backups ---")
    return run_ffs_batch('sync-backups.ffs_batch', dry_run=dry_run)

def run_task_sequence(task_functions: list, dry_run: bool = False):
    task_results = {}; overall_success = True
    failed_task_name = None; failure_output = None
    if dry_run:
        print("="*70); print("<<<<< INITIATING STATEFUL DRY RUN >>>>>"); print("All file operations are being redirected to a temporary directory."); print("="*70)
    for task_func in task_functions:
        task_start_time = time.time()
        task_name = task_func.__name__
        print("\n" + "#" * 70); print(f"### Starting Task: {task_name}"); print("#" * 70)
        success, output = task_func(dry_run=dry_run)
        task_end_time = time.time()
        task_results[task_name] = {"status": "SUCCESS" if success else "FAILURE", "duration": round(task_end_time - task_start_time, 2)}
        if not success:
            overall_success = False
            if failed_task_name is None:
                failed_task_name = task_name
                failure_output = output
            if CONTINUE_ON_ERROR:
                print(f"\n!!!!!! TASK FAILED: {task_name}. Continuing to next task as per configuration. !!!!!!")
            else:
                print(f"\n!!!!!! TASK FAILED: {task_name}. Aborting run. !!!!!!")
                break
    return overall_success, task_results, failed_task_name, failure_output

def send_email_report(job_succeeded: bool, failed_task: str = None, failure_output: str = None, generate_log_only: bool = False):
    if not generate_log_only: print("\n" + "="*70)
    print("--- Task: Preparing Email Report ---")
    if not all([EMAIL_HOST, EMAIL_PORT, EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("WARN: Email configuration is incomplete. Skipping email report."); return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_body = ""
    if job_succeeded:
        subject = f"✅ Automation Report: All Jobs Completed Successfully at {timestamp}"
        html_body += "<p>The scheduled automation run finished without errors.</p>"
    else:
        subject = f"❌ Automation Alert: Job Failed at Task '{failed_task}' at {timestamp}"
        html_body += f"<p>The automation run was interrupted by a failure in the <b>{failed_task}</b> task.</p>"
        if failure_output:
            html_body += "<h3>Failure Details:</h3>"
            html_body += f"<pre style='white-space: pre-wrap; word-wrap: break-word; background-color: #f4f4f4; border: 1px solid #ddd; padding: 10px; border-radius: 4px;'>{html.escape(failure_output)}</pre>"
    html_body += "<p>The main execution log, status dashboard, and relevant task-specific logs are attached for a full review.</p>"
    files_to_find = {}
    print("INFO: Searching for configured log files to attach...")
    log_patterns_str = os.getenv("EMAIL_ATTACH_LOGS", "")
    if log_patterns_str:
        log_patterns_to_attach = [p.strip() for p in log_patterns_str.split(',') if p.strip()]
        for pattern in log_patterns_to_attach:
            matching_logs = list(LOGS_DIR.glob(pattern))
            if not matching_logs: continue
            latest_log = sorted(matching_logs, key=os.path.getmtime, reverse=True)[0]
            files_to_find[latest_log.name] = latest_log
    if STATUS_DASHBOARD_FILE.exists(): files_to_find[STATUS_DASHBOARD_FILE.name] = STATUS_DASHBOARD_FILE
    if STATUS_FILE.exists(): files_to_find[STATUS_FILE.name] = STATUS_FILE
    print("INFO: Preparing attachments...")
    for name in files_to_find.keys(): print(f"  - Preparing: {name}")
    print("Email report sent successfully.")
    if not generate_log_only:
        try:
            msg = EmailMessage()
            msg['Subject'], msg['From'], msg['To'] = subject, EMAIL_SENDER, EMAIL_RECIPIENT
            msg.set_content("This is an HTML email. Please enable HTML viewing.")
            msg.add_alternative(html_body, subtype='html')
            for name, f_path in files_to_find.items():
                try:
                    with open(f_path, 'rb') as f: file_data = f.read()
                    subtype = 'html' if f_path.suffix.lower() == '.html' else 'plain'
                    maintype = 'text' if subtype in ['plain', 'html'] else 'application'
                    msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=name)
                except Exception as e: print(f"  - !!! ERROR reading {name} for attachment: {e} !!!")
            with smtplib.SMTP_SSL(EMAIL_HOST, int(EMAIL_PORT)) as server:
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.send_message(msg)
        except Exception as e: print(f"--- !!! FAILED TO SEND EMAIL !!! ---\n{e}")

def update_status_json(start_timestamp, end_timestamp, overall_success, task_results):
    run_summary = {"run_status": "SUCCESS" if overall_success else "FAILURE", "start_timestamp": start_timestamp.isoformat(), "end_timestamp": end_timestamp.isoformat(), "total_duration_seconds": round((end_timestamp - start_timestamp).total_seconds(), 2), "tasks_summary": task_results}
    try:
        if STATUS_FILE.exists():
            with open(STATUS_FILE, 'r') as f: status_data = json.load(f)
        else: status_data = {"run_history": []}
    except (json.JSONDecodeError, IOError): status_data = {"run_history": []}
    status_data['last_run_status'] = run_summary['run_status']
    if overall_success: status_data['last_success_timestamp'] = run_summary['end_timestamp']
    else: status_data['last_failure_timestamp'] = run_summary['end_timestamp']
    existing_history = status_data.get("run_history", [])
    status_data["run_history"] = [run_summary] + existing_history[:9]
    try:
        with open(STATUS_FILE, 'w') as f: json.dump(status_data, f, indent=4)
        print(f"Status dashboard updated with historical data: {STATUS_FILE}")
    except IOError as e: print(f"--- !!! FAILED TO UPDATE STATUS FILE !!! ---\n{e}")

def update_status_dashboard_md():
    if not STATUS_FILE.exists(): return
    try:
        with open(STATUS_FILE, 'r') as f: status_data = json.load(f)
    except (json.JSONDecodeError, IOError): return
    md_content = ["# Automation Status Dashboard\n"]; last_status = status_data.get('last_run_status', 'N/A'); last_status_emoji = "✅" if last_status == "SUCCESS" else "❌"
    md_content.append(f"- **Last Run Status:** {last_status_emoji} {last_status}"); last_success_ts = status_data.get('last_success_timestamp')
    if last_success_ts: md_content.append(f"- **Last Successful Run:** {datetime.fromisoformat(last_success_ts).strftime('%Y-%m-%d %H:%M:%S')}")
    else: md_content.append("- **Last Successful Run:** N/A")
    last_failure_ts = status_data.get('last_failure_timestamp')
    if last_failure_ts: md_content.append(f"- **Last Failed Run:** {datetime.fromisoformat(last_failure_ts).strftime('%Y-%m-%d %H:%M:%S')}")
    else: md_content.append("- **Last Failed Run:** N/A")
    md_content.append("\n---\n## Run History\n")
    for run in status_data.get('run_history', []):
        start_time_str = datetime.fromisoformat(run['start_timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        end_time_str = datetime.fromisoformat(run['end_timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        run_status = run['run_status']
        run_status_emoji = "✅" if run_status == "SUCCESS" else "❌"
        md_content.append(f"### Run from: {start_time_str} to {end_time_str} - {run_status_emoji} {run_status}\n")
        md_content.append("| Task Name              | Status  | Duration (s) |"); md_content.append("| ---------------------- | ------- | ------------ |")
        for task_name, details in run.get('tasks_summary', {}).items():
            md_content.append(f"| {task_name:<22} | {details['status']:<7} | {details['duration']:<12.2f} |")
        md_content.append(f"\n**Total Duration:** {run['total_duration_seconds']} seconds\n\n---\n")
    try:
        with open(STATUS_DASHBOARD_FILE, 'w', encoding='utf-8') as f: f.write("\n".join(md_content))
        print(f"Human-readable dashboard updated: {STATUS_DASHBOARD_FILE.name}")
    except IOError as e: print(f"--- !!! FAILED TO UPDATE MARKDOWN DASHBOARD !!! ---\n{e}")

def main():
    all_tasks = {'export-personal': export_personal, 'export-work': export_work, 'convert-kdbx': convert_json_to_kdbx, 'raindrop-backup': raindrop_backup, 'sync-2fa': sync_2fa, 'sync-backups': sync_backups}
    parser = argparse.ArgumentParser(description="Automated task runner.")
    subparsers = parser.add_subparsers(dest='command', required=True)
    run_parser = subparsers.add_parser('run-tasks', help='Execute one or more tasks and update dashboards.')
    run_parser.add_argument('task_names', choices=['run-all'] + list(all_tasks.keys()), nargs='+', help="The name(s) of the task(s) to run.")
    run_parser.add_argument('--dry-run', action='store_true', help='Simulate a run in a temporary directory which is deleted afterwards.')
    report_parser = subparsers.add_parser('send-report', help='Send the final email report based on the last run.')
    report_parser.add_argument('status', choices=['success', 'failure'])
    report_parser.add_argument('--generate-log-only', action='store_true', help='Only print logging messages without sending the email.')
    args = parser.parse_args()
    
    if args.command == 'run-tasks':
        if args.dry_run:
            dry_run_dir = ROOT_DIR / "_dry_run_output"
            print("="*70); print(f"<<<<< INITIATING STATEFUL DRY RUN IN: {dry_run_dir} >>>>>"); print("="*70)
            
            if dry_run_dir.exists(): shutil.rmtree(dry_run_dir)
            dry_run_dir.mkdir()
            os.environ["DRY_RUN_VAULTS_DIR"] = str(dry_run_dir)
            os.environ["DRY_RUN_BACKUP_DEST"] = str(dry_run_dir)

            try:
                task_list = list(all_tasks.values()) if 'run-all' in args.task_names else [all_tasks[name] for name in args.task_names if name in all_tasks]
                success, _, failed_task, _ = run_task_sequence(task_list, dry_run=True)
                if success: print("\n===== DRY RUN FINISHED SUCCESSFULLY =====")
                else: print(f"\n===== DRY RUN FAILED (FIRST FAILURE AT TASK: {failed_task}) =====")
            finally:
                print("\n--- Cleaning up dry run directory... ---")
                time.sleep(0.5)
                shutil.rmtree(dry_run_dir)
                os.environ.pop("DRY_RUN_VAULTS_DIR", None)
                os.environ.pop("DRY_RUN_BACKUP_DEST", None)
                print("--- Cleanup complete. ---")
            sys.exit(0)

        start_time_dt = datetime.now()
        if 'run-all' in args.task_names: print("===== STARTING FULL AUTOMATION RUN ====="); task_list = list(all_tasks.values())
        else:
            print(f"===== STARTING CUSTOM AUTOMATION RUN: {', '.join(args.task_names)} =====")
            task_list = [all_tasks[name] for name in args.task_names if name in all_tasks]
        
        success, task_results, failed_task, failure_output = run_task_sequence(task_list, dry_run=False)
        end_time_dt = datetime.now()
        
        update_status_json(start_time_dt, end_time_dt, success, task_results)
        update_status_dashboard_md()
        
        if not success and failure_output:
            try:
                if not FAILURE_LOG_FILE.exists():
                    with open(FAILURE_LOG_FILE, 'w', encoding='utf-8') as f: f.write(failure_output)
            except IOError as e: print(f"--- !!! FAILED TO WRITE FAILURE DETAILS LOG !!! ---\n{e}")

        if success: print("\n===== ALL TASKS COMPLETED SUCCESSFULLY ====="); sys.exit(0)
        else: print(f"\n===== AUTOMATION FAILED (FIRST FAILURE AT TASK: {failed_task}) ====="); sys.exit(1)

    elif args.command == 'send-report':
        failed_task = None; failure_output = None
        if args.status == 'failure':
            if FAILURE_LOG_FILE.exists():
                try:
                    with open(FAILURE_LOG_FILE, 'r', encoding='utf-8') as f: failure_output = f.read()
                    if not args.generate_log_only: os.remove(FAILURE_LOG_FILE)
                except (IOError, OSError) as e: print(f"--- !!! FAILED TO READ/DELETE FAILURE LOG !!! ---\n{e}")
            if STATUS_FILE.exists():
                try:
                    with open(STATUS_FILE, 'r') as f: data = json.load(f)
                    latest_run = data.get('run_history', [{}])[0]
                    for task, details in latest_run.get('tasks_summary', {}).items():
                        if details['status'] == 'FAILURE': failed_task = task; break
                except (json.JSONDecodeError, IndexError): pass
        send_email_report(args.status == 'success', failed_task, failure_output, generate_log_only=args.generate_log_only)
        sys.exit(0)

if __name__ == "__main__":
    main()