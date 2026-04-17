#!/bin/bash

# --- CONFIGURATION ---
PROJECT_DIR="$(dirname "$(realpath "$0")")"
VC_CONTAINER="$PROJECT_DIR/vaults.hc"
MOUNT_POINT="/mnt/secure_vaults"
SECRET_FILE="/root/.vc_secret"

# --- MODE SELECTION ---
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

# 1. MOUNT VERACRYPT (Clean Logic)
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

# 2. LINK FOLDERS
echo "Linking folders..." >> "$LOG_FILE"
rm -rf "$PROJECT_DIR/vaults" "$PROJECT_DIR/2fa" "$PROJECT_DIR/backups" "$PROJECT_DIR/.env"
ln -sfn "$MOUNT_POINT/vaults"  "$PROJECT_DIR/vaults"
ln -sfn "$MOUNT_POINT/2fa"     "$PROJECT_DIR/2fa"
ln -sfn "$MOUNT_POINT/backups" "$PROJECT_DIR/backups"
ln -sf "$MOUNT_POINT/.env"     "$PROJECT_DIR/.env"

# 3. RUN PYTHON TASKS
echo "Running Python Engine..." >> "$LOG_FILE"
source "$PROJECT_DIR/venv/bin/activate"

if [ "$MODE" == "DRY_RUN" ]; then
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all --dry-run >> "$LOG_FILE" 2>&1
    PYTHON_EXIT_CODE=$?
else
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all >> "$LOG_FILE" 2>&1
    PYTHON_EXIT_CODE=$?
fi

# --- NEW STEP: PRESERVE SECRETS FOR EMAIL ---
# We need the .env file to send the email, but we must unmount the container first.
# We copy .env to a temp file readable ONLY by the current user.
if [ -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.temp_env_handoff"
    chmod 600 "$PROJECT_DIR/.temp_env_handoff"
fi

# 4. UNMOUNT
echo "Unmounting container..." >> "$LOG_FILE"
rm "$PROJECT_DIR/vaults" 2>/dev/null
rm "$PROJECT_DIR/2fa" 2>/dev/null
rm "$PROJECT_DIR/backups" 2>/dev/null
rm "$PROJECT_DIR/.env" 2>/dev/null

sudo veracrypt --text --non-interactive --dismount "$VC_CONTAINER" >> "$LOG_FILE" 2>&1

# 5. BACKUP CONTAINER (Copy & Rotate)
FINAL_EXIT_CODE=$PYTHON_EXIT_CODE

if [ "$MODE" == "NORMAL" ]; then
    if [ $PYTHON_EXIT_CODE -eq 0 ]; then
        echo "Starting Container Backup..." >> "$LOG_FILE"
        
        if [ -d "$BACKUP_DEST" ]; then
            # Clean old backups
            echo "Cleaning up old container backups in destination..." >> "$LOG_FILE"
            find "$BACKUP_DEST" -maxdepth 1 -name "ctrl_s_master_*.hc" -type f -not -name "$BACKUP_FILENAME" -delete
            
            # Copy new one
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

# 6. SEND REPORT
# Link the temp env file back so Python can read it
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

# 7. FINAL CLEANUP
# Securely remove the temp secret file
rm "$PROJECT_DIR/.env" 2>/dev/null
rm "$PROJECT_DIR/.temp_env_handoff" 2>/dev/null

echo "--- Finished at $(date) ---" >> "$LOG_FILE"
exit $FINAL_EXIT_CODE
