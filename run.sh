#!/bin/bash

# =================================================================
#               MASTER AUTOMATION SCRIPT (SUPERVISOR)
# =================================================================

# --- 1. CONFIGURATION ---
PROJECT_DIR="$(dirname "$(realpath "$0")")"
VC_CONTAINER="$PROJECT_DIR/vaults.hc"
MOUNT_POINT="/mnt/secure_vaults"
SECRET_FILE="/root/.vc_secret"

# List of folders to link from the Vault (Add new ones here)
SECURE_FOLDERS=("vaults" "2fa" "backups")

# Mode Selection
MODE="NORMAL"
LOG_FILE="$PROJECT_DIR/_logs/run_$(date +%Y%m%d_%H%M%S).log"
BACKUP_DEST="/data/assets/syncthing/My_Shit"

# File formatting
TODAY=$(date +%Y-%m-%d)
BACKUP_FILENAME="ctrl_s_master_${TODAY}.hc"

if [ "$1" == "dry" ]; then
    MODE="DRY_RUN"
    LOG_FILE="/dev/stdout"
    echo "!!! RUNNING IN DRY RUN MODE !!!"
else
    mkdir -p "$PROJECT_DIR/_logs"
fi

mkdir -p "$MOUNT_POINT"

echo "--- Starting Run at $(date) ---" >> "$LOG_FILE"

# --- 2. MOUNT VERACRYPT ---
# Pre-cleanup
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
# Loop through the secure folders list to create symlinks
for folder in "${SECURE_FOLDERS[@]}"; do
    rm -rf "$PROJECT_DIR/$folder"
    ln -sfn "$MOUNT_POINT/$folder" "$PROJECT_DIR/$folder"
done
# Link the .env file separately (since it's a file, not a directory)
rm -f "$PROJECT_DIR/.env"
ln -sf "$MOUNT_POINT/.env" "$PROJECT_DIR/.env"

# --- 4. RUN PYTHON TASKS ---
echo "Running Python Engine..." >> "$LOG_FILE"
source "$PROJECT_DIR/venv/bin/activate"

if [ "$MODE" == "DRY_RUN" ]; then
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all --dry-run >> "$LOG_FILE" 2>&1
    PYTHON_EXIT_CODE=$?
else
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all >> "$LOG_FILE" 2>&1
    PYTHON_EXIT_CODE=$?
fi

# --- PRESERVE SECRETS FOR EMAIL ---
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
            echo "Cleaning up old container backups in destination..." >> "$LOG_FILE"
            find "$BACKUP_DEST" -maxdepth 1 -name "ctrl_s_master_*.hc" -type f -not -name "$BACKUP_FILENAME" -delete
            
            echo "Copying to: $BACKUP_DEST/$BACKUP_FILENAME" >> "$LOG_FILE"
            cp "$VC_CONTAINER" "$BACKUP_DEST/$BACKUP_FILENAME"
            
            if [ $? -eq 0 ]; then
                echo "✅ Container Backup Successful." >> "$LOG_FILE"
            else
                echo "❌ ERROR: Failed to copy container file." >> "$LOG_FILE"
                FINAL_EXIT_CODE=1
            fi
        else
            echo "❌ ERROR: Backup destination not found: $BACKUP_DEST" >> "$LOG_FILE"
            FINAL_EXIT_CODE=1
        fi
    else
        echo "⚠️ Skipping Container Backup because Python tasks failed." >> "$LOG_FILE"
    fi
else
    echo "--- DRY RUN: Skipped container copy & rotation ---" >> "$LOG_FILE"
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
rm "$PROJECT_DIR/.env" 2>/dev/null
rm "$PROJECT_DIR/.temp_env_handoff" 2>/dev/null

echo "--- Finished at $(date) ---" >> "$LOG_FILE"
exit $FINAL_EXIT_CODE