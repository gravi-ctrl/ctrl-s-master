#!/bin/bash

# =================================================================
#               MASTER AUTOMATION SCRIPT (SUPERVISOR)
# =================================================================

# --- 1. CONFIGURATION ---
PROJECT_DIR="$(dirname "$(realpath "$0")")"
VC_CONTAINER="$PROJECT_DIR/vaults.hc"
MOUNT_POINT="/mnt/secure_vaults"
SECRET_FILE="/root/.vc_secret"
SECURE_FOLDERS=("vaults" "2fa" "backups")

# Notify file: used ONLY for fatal pre-load failures (before .env is in RAM).
# Contains two lines:  BOT_TOKEN=xxxxx  and  CHAT_ID=xxxxx
# Intentionally minimal and stored separately from the container.
NOTIFY_FILE="/root/.vc_notify"

MODE="NORMAL"
LOG_FILE="$PROJECT_DIR/_logs/run_$(date +%Y%m%d_%H%M%S).log"
BACKUP_DEST="/data/assets/syncthing/My_Shit"
TODAY=$(date +%Y-%m-%d)
BACKUP_FILENAME="ctrl_s_master_${TODAY}.hc"

if [ "$1" == "dry" ]; then
    MODE="DRY_RUN"
    LOG_FILE="/dev/stdout"
fi

mkdir -p "$PROJECT_DIR/_logs"
mkdir -p "$MOUNT_POINT"
echo "--- Starting Run at $(date) ---" >> "$LOG_FILE"

# =================================================================
# --- FATAL NOTIFICATION ---
# Fires a Telegram message for pre-load failures only (before .env
# is in RAM and the normal send-report channel is available).
# Reads BOT_TOKEN and CHAT_ID from /root/.vc_notify.
# Completely silent if the file does not exist — nothing breaks.
# =================================================================
notify_fatal() {
    local msg="$1"
    [ -f "$NOTIFY_FILE" ] || return 0
    local token chat_id
    token=$(grep '^BOT_TOKEN=' "$NOTIFY_FILE" | cut -d= -f2)
    chat_id=$(grep '^CHAT_ID=' "$NOTIFY_FILE" | cut -d= -f2)
    [ -z "$token" ] || [ -z "$chat_id" ] && return 0
    curl -fsS "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${chat_id}" \
        -d "text=[$(hostname)] ctrl_s_master FATAL: ${msg}" \
        > /dev/null 2>&1
}

# =================================================================
# --- FAIL-SAFE TRAP (Handles Ctrl+C, SSH Drops, and Crashes) ---
# =================================================================
cleanup() {
    echo "Running emergency/final cleanup..." >> "$LOG_FILE"

    # 1. Remove folder symlinks (rm -rf on a symlink removes the link,
    #    never follows into the target — the container data is safe)
    for folder in "${SECURE_FOLDERS[@]}"; do
        rm -rf "$PROJECT_DIR/$folder" 2>/dev/null
    done

    # 2. Force dismount container
    sudo veracrypt --text --non-interactive --dismount "$VC_CONTAINER" >/dev/null 2>&1
}
# EXIT = normal end, INT = Ctrl+C, TERM = kill command, HUP = SSH disconnect
trap cleanup EXIT INT TERM HUP
# =================================================================


# --- 2. MOUNT VERACRYPT ---
sudo veracrypt -d "$VC_CONTAINER" > /dev/null 2>&1
sudo dmsetup remove_all > /dev/null 2>&1
sleep 2

echo "Mounting container..." >> "$LOG_FILE"
sudo veracrypt --text --non-interactive --pim=0 --keyfiles="" --protect-hidden=no \
    -m=nokernelcrypto \
    --password=$(sudo cat $SECRET_FILE) "$VC_CONTAINER" "$MOUNT_POINT" >> "$LOG_FILE" 2>&1

if [ $? -ne 0 ]; then
    echo "FATAL: Failed to mount VeraCrypt container." >> "$LOG_FILE"
    notify_fatal "Failed to mount VeraCrypt container"
    exit 1
fi

# --- 3. LINK FOLDERS AND LOAD SECRETS INTO RAM ---
echo "Linking folders..." >> "$LOG_FILE"
for folder in "${SECURE_FOLDERS[@]}"; do
    rm -rf "$PROJECT_DIR/$folder"
    ln -sfn "$MOUNT_POINT/$folder" "$PROJECT_DIR/$folder"
done

# Load .env from the container into this shell's environment (RAM only).
# dotenv_values() handles quoted values, inline comments, CRLF line endings,
# and values containing shell-special characters correctly.
# The _VC_ENV_OK sentinel confirms the Python command completed successfully.
echo "Loading secrets into RAM..." >> "$LOG_FILE"
_env_exports=$(
    "$PROJECT_DIR/venv/bin/python3" -c "
from dotenv import dotenv_values
import shlex, sys
try:
    d = dotenv_values('$MOUNT_POINT/.env')
    for k, v in d.items():
        if v is not None:
            print(f'export {k}={shlex.quote(v)}')
    print('export _VC_ENV_OK=1')
except Exception as e:
    print(f'FATAL: .env parse error: {e}', file=sys.stderr)
    sys.exit(1)
" 2>>"$LOG_FILE"
)

eval "$_env_exports"

if [ -z "$_VC_ENV_OK" ]; then
    echo "FATAL: Failed to load .env from container - check log above for Python error." >> "$LOG_FILE"
    notify_fatal "Failed to load .env from container"
    exit 1
fi
unset _VC_ENV_OK

# --- 4. RUN PYTHON TASKS ---
# From this point on all secrets are in RAM. Failures beyond here are handled
# by the normal send-report flow which already has notification credentials.
echo "Running Python Engine..." >> "$LOG_FILE"
source "$PROJECT_DIR/venv/bin/activate"

if [ "$MODE" == "DRY_RUN" ]; then
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all --dry-run >> "$LOG_FILE" 2>&1
else
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all >> "$LOG_FILE" 2>&1
fi
PYTHON_EXIT_CODE=$?

# --- 5. UNMOUNT ---
# Secrets remain alive in this shell's environment (RAM).
# No .temp_env_handoff file is needed -- send-report (step 7) runs in this
# same shell process and inherits all variables automatically.
echo "Unmounting container..." >> "$LOG_FILE"
for folder in "${SECURE_FOLDERS[@]}"; do
    rm -rf "$PROJECT_DIR/$folder" 2>/dev/null
done
sudo veracrypt --text --non-interactive --dismount "$VC_CONTAINER" >> "$LOG_FILE" 2>&1

# --- 6. BACKUP CONTAINER ---
FINAL_EXIT_CODE=$PYTHON_EXIT_CODE

if [ "$MODE" == "NORMAL" ]; then
    if [ $PYTHON_EXIT_CODE -eq 0 ]; then
        echo "Starting Container Backup..." >> "$LOG_FILE"
        if [ -d "$BACKUP_DEST" ]; then
            find "$BACKUP_DEST" -maxdepth 1 -name "ctrl_s_master_*.hc" -type f -not -name "$BACKUP_FILENAME" -delete
            cp "$VC_CONTAINER" "$BACKUP_DEST/$BACKUP_FILENAME"
            [ $? -eq 0 ] || FINAL_EXIT_CODE=1
        fi
    fi
fi

# --- 7. SEND REPORT ---
# Env vars are still live in this shell — Python inherits them directly.
if [ "$MODE" == "NORMAL" ]; then
    if [ $FINAL_EXIT_CODE -eq 0 ]; then
        python3 "$PROJECT_DIR/src/master_automation.py" send-report success >> "$LOG_FILE" 2>&1
    else
        python3 "$PROJECT_DIR/src/master_automation.py" send-report failure >> "$LOG_FILE" 2>&1
    fi
fi

# --- 8. FINAL CLEANUP ---
# Handled entirely by the trap on EXIT — nothing to shred here.
echo "--- Finished at $(date) ---" >> "$LOG_FILE"
exit $FINAL_EXIT_CODE
