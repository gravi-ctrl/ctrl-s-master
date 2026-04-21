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
# --- FAIL-SAFE TRAP (Handles Ctrl+C, SSH Drops, and Crashes) ---
# =================================================================
cleanup() {
    echo "Running emergency/final cleanup..." >> "$LOG_FILE"
    
    # 1. Remove folder pointers
    for folder in "${SECURE_FOLDERS[@]}"; do
        rm -rf "$PROJECT_DIR/$folder" 2>/dev/null
    done
    
    # 2. Securely shred credentials
    rm -f "$PROJECT_DIR/.env" 2>/dev/null
    shred -u "$PROJECT_DIR/.temp_env_handoff" 2>/dev/null || rm -f "$PROJECT_DIR/.temp_env_handoff" 2>/dev/null
    
    # 3. Force dismount container (Silenced to prevent log noise on normal exits)
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
    exit 1  
fi

# --- 3. LINK FOLDERS ---
echo "Linking folders..." >> "$LOG_FILE"
for folder in "${SECURE_FOLDERS[@]}"; do
    rm -rf "$PROJECT_DIR/$folder"
    ln -sfn "$MOUNT_POINT/$folder" "$PROJECT_DIR/$folder"
done
rm -f "$PROJECT_DIR/.env"
ln -sf "$MOUNT_POINT/.env" "$PROJECT_DIR/.env"

# --- 4. RUN PYTHON TASKS ---
echo "Running Python Engine..." >> "$LOG_FILE"
source "$PROJECT_DIR/venv/bin/activate"

if [ "$MODE" == "DRY_RUN" ]; then
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all --dry-run >> "$LOG_FILE" 2>&1
else
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all >> "$LOG_FILE" 2>&1
fi
PYTHON_EXIT_CODE=$?

# Preserve secrets for email
if [ -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.temp_env_handoff"
    chmod 600 "$PROJECT_DIR/.temp_env_handoff"
fi

# --- 5. UNMOUNT ---
echo "Unmounting container..." >> "$LOG_FILE"
for folder in "${SECURE_FOLDERS[@]}"; do
    rm "$PROJECT_DIR/$folder" 2>/dev/null
done
rm "$PROJECT_DIR/.env" 2>/dev/null
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
if [ -f "$PROJECT_DIR/.temp_env_handoff" ]; then
    ln -sf "$PROJECT_DIR/.temp_env_handoff" "$PROJECT_DIR/.env"
fi

if [ "$MODE" == "NORMAL" ]; then
    if [ $FINAL_EXIT_CODE -eq 0 ]; then
        python3 "$PROJECT_DIR/src/master_automation.py" send-report success >> "$LOG_FILE" 2>&1
    else
        python3 "$PROJECT_DIR/src/master_automation.py" send-report failure >> "$LOG_FILE" 2>&1
    fi
fi

# --- 8. FINAL CLEANUP ---
# The logic here is now handled by the trap function on script exit
echo "--- Finished at $(date) ---" >> "$LOG_FILE"
exit $FINAL_EXIT_CODE