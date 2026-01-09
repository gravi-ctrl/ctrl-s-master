#!/bin/bash

# --- CONFIGURATION ---
PROJECT_DIR="/home/gravi-ctrl/scripts/ctrl_s_master"
VC_CONTAINER="$PROJECT_DIR/vaults.hc"
MOUNT_POINT="/mnt/secure_vaults"
SECRET_FILE="/root/.vc_secret"

# --- MODE SELECTION ---
# Default to NORMAL mode
MODE="NORMAL"
LOG_FILE="$PROJECT_DIR/_logs/run_$(date +%Y%m%d_%H%M%S).log"
BACKUP_DEST="/srv/data/assets/syncthing/My_Shit"

# If "dry" is passed as argument, switch to DRY RUN mode
if [ "$1" == "dry" ]; then
    MODE="DRY_RUN"
    LOG_FILE="/dev/stdout"  # Redirect all logs to the screen
    echo "!!! RUNNING IN DRY RUN MODE - NO CHANGES WILL BE MADE !!!"
else
    # Only create log directory in Normal mode
    mkdir -p "$PROJECT_DIR/_logs"
fi

mkdir -p "$MOUNT_POINT"

# Start Logging
echo "--- Starting Run at $(date) ---" >> "$LOG_FILE"

# 1. MOUNT VERACRYPT
# --- PRE-CLEANUP: Prevent "device-mapper" zombie locks ---
# We force a dismount of this specific container just in case a previous run didn't finish cleanly.
sudo veracrypt -d "$VC_CONTAINER" > /dev/null 2>&1
# Give the kernel a moment to release the device mapper
sleep 2 
# -------------------------------------------------------

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

# 3. RUN ENGINE
echo "Running Python Engine..." >> "$LOG_FILE"
source "$PROJECT_DIR/venv/bin/activate"

if [ "$MODE" == "DRY_RUN" ]; then
    # Run Python with --dry-run flag
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all --dry-run >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
else
    # Run Python normally
    python3 "$PROJECT_DIR/src/master_automation.py" run-tasks run-all >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
fi

# 4. REPORTING
# We skip email reporting in Dry Run to avoid spamming you
if [ "$MODE" == "NORMAL" ]; then
    if [ $EXIT_CODE -eq 0 ]; then
        python3 "$PROJECT_DIR/src/master_automation.py" send-report success >> "$LOG_FILE" 2>&1
    else
        python3 "$PROJECT_DIR/src/master_automation.py" send-report failure >> "$LOG_FILE" 2>&1
    fi
fi

# 5. UNMOUNT (CRITICAL STEP)
echo "Unmounting container..." >> "$LOG_FILE"
rm "$PROJECT_DIR/vaults" 2>/dev/null
rm "$PROJECT_DIR/2fa" 2>/dev/null
rm "$PROJECT_DIR/backups" 2>/dev/null
rm "$PROJECT_DIR/.env" 2>/dev/null

sudo veracrypt --text --non-interactive --dismount "$VC_CONTAINER" >> "$LOG_FILE" 2>&1

# 6. BACKUP CONTAINER
# Only copy the container if we are in NORMAL mode AND python succeeded.
if [ "$MODE" == "NORMAL" ]; then
    if [ $EXIT_CODE -eq 0 ]; then
        echo "Replicating encrypted container..." >> "$LOG_FILE"
        if [ -d "$BACKUP_DEST" ]; then
            cp "$VC_CONTAINER" "$BACKUP_DEST/ctrl_s_master.hc"
            echo "Container copied to $BACKUP_DEST" >> "$LOG_FILE"
        else
            echo "ERROR: Backup destination not found: $BACKUP_DEST" >> "$LOG_FILE"
        fi
    fi
else
    echo "--- DRY RUN COMPLETE: Skipped container replication (cp) ---" >> "$LOG_FILE"
fi

echo "--- Finished at $(date) ---" >> "$LOG_FILE"
exit $EXIT_CODE
