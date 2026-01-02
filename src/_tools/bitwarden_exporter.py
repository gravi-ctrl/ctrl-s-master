#!/usr/bin/env python3
import os
import sys
import subprocess
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from bitwarden_sdk import BitwardenClient, client_settings_from_dict

# Fix: Look in the SAME directory for common_utils
sys.path.append(str(Path(__file__).resolve().parent))
from common_utils import rotate_backups

def load_master_env():
    root_path_str = os.getenv("AUTOMATION_ROOT")
    if root_path_str:
        root_path = Path(root_path_str)
    else:
        # Fallback for manual running
        script_path = Path(__file__).resolve()
        root_path = script_path.parents[2]
    
    env_path = root_path / '.env'
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)
        return root_path
    else:
        print(f"FATAL: Master .env file not found at expected path: {env_path}")
        sys.exit(1)

def determine_output_path(root_path: Path, vault_type: str):
    prefix = f"BW_{vault_type.upper()}_"
    
    vaults_dir_override = os.getenv("DRY_RUN_VAULTS_DIR")
    if vaults_dir_override:
        vaults_dir = Path(vaults_dir_override)
    else:
        vaults_dir = root_path / os.getenv("BW_VAULTS_DIR", "vaults")

    output_dir = vaults_dir / "json"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    base_filename, ext = os.path.splitext(os.getenv(f"{prefix}OUTPUT_FILENAME"))
    
    serial_number = 1
    glob_pattern = f"*-{base_filename}_*.json"
    existing_files = [f.name for f in output_dir.glob(glob_pattern)]
    if existing_files:
        serials = [int(f.split('-')[0]) for f in existing_files if f.split('-')[0].isdigit()]
        if serials:
            serial_number = max(serials) + 1

    current_date = datetime.now().strftime("%d-%m-%Y")
    final_filename = f"{serial_number}-{base_filename}_{current_date}{ext}"
    return output_dir / final_filename, output_dir, f"*-{base_filename}_*{ext}"

def get_config(vault_type: str, root_path: Path):
    prefix = f"BW_{vault_type.upper()}_"
    required_vars = [
        "BW_CLI_PATH", "BW_API_URL", "BW_IDENTITY_URL", "BW_ACCESS_TOKEN", 
        "BW_STATE_FILE", f"{prefix}CLIENT_ID_UUID", f"{prefix}CLIENT_SECRET_UUID", 
        f"{prefix}MASTER_PASSWORD_UUID", f"BITWARDEN_{vault_type.upper()}_PASSWORD"
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"FATAL: Missing env vars: {', '.join(missing_vars)}")
        sys.exit(1)

    config = {}
    cli_path = os.getenv("BW_CLI_PATH", "bw")
    config["bw_command"] = root_path / cli_path if Path(cli_path).is_absolute() else Path(cli_path)
    
    config["api_url"] = os.getenv("BW_API_URL")
    config["identity_url"] = os.getenv("BW_IDENTITY_URL")
    config["access_token"] = os.getenv("BW_ACCESS_TOKEN")
    config["state_file"] = root_path / "src" / os.getenv("BW_STATE_FILE")
    try:
        config["serials_to_keep"] = int(os.getenv("BW_SERIALS_TO_KEEP", 0))
    except (ValueError, TypeError):
        config["serials_to_keep"] = 0

    config["client_id_uuid"] = os.getenv(f"{prefix}CLIENT_ID_UUID")
    config["client_secret_uuid"] = os.getenv(f"{prefix}CLIENT_SECRET_UUID")
    config["master_password_uuid"] = os.getenv(f"{prefix}MASTER_PASSWORD_UUID")
    config["export_format"] = os.getenv(f"{prefix}EXPORT_FORMAT", "json")
    config["is_organization"] = os.getenv(f"{prefix}IS_ORGANIZATION", "false").lower() == "true"
    config["organization_id"] = os.getenv(f"{prefix}ORGANIZATION_ID")
    config["export_password"] = os.getenv(f"BITWARDEN_{vault_type.upper()}_PASSWORD")

    output_file_path, output_dir, glob_pattern = determine_output_path(root_path, vault_type)
    config["output_file"] = output_file_path
    config["output_dir"] = output_dir
    config["glob_pattern"] = glob_pattern

    return config

def authenticate_secrets_manager(config):
    print("[AUTH] Authenticating with Bitwarden Secrets Manager...")
    client = BitwardenClient(client_settings_from_dict({"apiUrl": config["api_url"], "identityUrl": config["identity_url"], "deviceType": "SDK", "userAgent": "Python Script"}))
    client.auth().login_access_token(config["access_token"], str(config["state_file"]))
    return client

def get_secret_by_uuid(client: BitwardenClient, secret_uuid: str, secret_name: str):
    if not secret_uuid:
        print(f"FATAL: UUID for '{secret_name}' is not configured.")
        sys.exit(1)
    print(f"[FETCH] Fetching secret: '{secret_name}'")
    try:
        secret = client.secrets().get(secret_uuid)
        if secret and secret.data and secret.data.value:
            return secret.data.value
        print(f"FATAL: Failed to retrieve value for secret '{secret_name}'.")
        sys.exit(1)
    except Exception as e:
        print(f"FATAL: Error fetching secret '{secret_name}': {e}")
        sys.exit(1)

def bw_logout(config):
    try:
        subprocess.run([str(config["bw_command"]), "logout"], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        pass

def bw_login(config, client_id, client_secret):
    try:
        print("[AUTH] Logging in to Bitwarden using API key...")
        os.environ['BW_CLIENTID'] = client_id
        os.environ['BW_CLIENTSECRET'] = client_secret
        subprocess.run([str(config["bw_command"]), "login", "--apikey"], check=True, capture_output=True, text=True)
        print("OK: Logged in. Syncing...")
        subprocess.run([str(config["bw_command"]), "sync"], check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FATAL: Failed to log in: {e.stderr.strip()}")
        sys.exit(1)

def bw_unlock(config, master_password):
    try:
        print("[STATUS] Unlocking vault...")
        os.environ["BW_MASTER_PASSWORD"] = master_password
        result = subprocess.run([str(config["bw_command"]), "unlock", "--passwordenv", "BW_MASTER_PASSWORD", "--raw"], check=True, capture_output=True, text=True)
        session_key = result.stdout.strip()
        if session_key:
            os.environ["BW_SESSION"] = session_key
            return session_key
        print("FATAL: Session key not found.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"FATAL: Error unlocking vault: {e.stderr.strip()}")
        sys.exit(1)

def export_vault(config, session_key):
    try:
        print(f"[EXPORT] Exporting to {config['output_file']}...")
        export_command = [
            str(config["bw_command"]), "export", 
            "--format", config["export_format"], 
            "--output", str(config["output_file"]), 
            "--session", session_key
        ]
        if config["export_format"] == "encrypted_json" and config["export_password"]:
            export_command.extend(["--password", config["export_password"]])
        if config["is_organization"] and config["organization_id"]:
            export_command.extend(["--organizationid", config["organization_id"]])
        
        subprocess.run(export_command, check=True, capture_output=True, text=True)
        print(f"OK: Export successful.")
    except subprocess.CalledProcessError as e:
        print(f"FATAL: Error exporting vault: {e.stderr.strip()}")
        sys.exit(1)

if __name__ == "__main__":
    import shutil
    if len(sys.argv) < 2 or sys.argv[1] not in ['personal', 'work']:
        print("FATAL: Provide 'personal' or 'work' as argument.")
        sys.exit(1)

    vault_type = sys.argv[1]
    print(f"\n--- Starting Bitwarden Export: {vault_type.upper()} ---")

    config = None
    session_key = None
    try:
        project_root_path = load_master_env()
        config = get_config(vault_type, project_root_path)
        print(f"OK: Using Bitwarden CLI at: {config['bw_command']}")
        bw_logout(config)
        secrets_client = authenticate_secrets_manager(config)
        client_id = get_secret_by_uuid(secrets_client, config["client_id_uuid"], f"{vault_type}_client_id")
        client_secret = get_secret_by_uuid(secrets_client, config["client_secret_uuid"], f"{vault_type}_client_secret")
        master_password = get_secret_by_uuid(secrets_client, config["master_password_uuid"], f"{vault_type}_master_password")
        bw_login(config, client_id, client_secret)
        session_key = bw_unlock(config, master_password)
        export_vault(config, session_key)
        print("\n[CLEANUP] Rotating serial backups...")
        rotate_backups(config["output_dir"], config["glob_pattern"], config["serials_to_keep"])
    finally:
        if session_key:
            print("[CLEANUP] Wiping session key...")
            os.environ.pop("BW_SESSION", None)
        if config:
            bw_logout(config)
    print(f"--- Finished: {vault_type.upper()} ---")