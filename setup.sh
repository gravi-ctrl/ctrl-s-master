#!/bin/bash
set -e

# =================================================================
#               PROJECT MASTER SETUP & UPDATER
# =================================================================
# Usage:
#   sudo ./setup.sh          - Standard setup / Quick health check
#   sudo ./setup.sh -f       - Force rebuild (Nuclear update)
# =================================================================

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_DIR="$PROJECT_DIR/venv"
LOG_DIR="$PROJECT_DIR/_logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/setup_$(date +%Y%m%d_%H%M%S).log"

# Check for the Force / Rebuild flag
FORCE_REBUILD=0
if [ "$1" == "-f" ] || [ "$1" == "--force" ]; then
    FORCE_REBUILD=1
fi

echo "Initializing environment..."
echo "This window will remain blank. Detailed log will be at:"
echo "$LOG_FILE"

{
    echo "========================================"
    echo "   CTRL_S_MASTER: SETUP & UPDATE"
    echo "   DATE: $(date)"
    if [ $FORCE_REBUILD -eq 1 ]; then
        echo "   MODE: FORCE REBUILD (-f)"
    else
        echo "   MODE: STANDARD HEALTH-CHECK"
    fi
    echo "========================================"

    # --- 1. Install System Dependencies ---
    echo "[1/5] Checking System Prerequisites..."
    sudo apt-get update -y
    sudo apt-get install -y software-properties-common coreutils python3-venv python3-pip unzip rsync curl dos2unix

    # --- 2. Install VeraCrypt ---
    echo "[2/5] Checking VeraCrypt..."
    if ! command -v veracrypt &> /dev/null; then
        echo "      - VeraCrypt not found. Adding PPA and installing..."
        sudo add-apt-repository ppa:unit193/encryption -y
        sudo apt-get update -y
        sudo apt-get install -y veracrypt
    else
        echo "      - VeraCrypt is already installed."
    fi

    # --- 3. Install/Update Bitwarden CLI ---
    echo "[3/5] Checking Bitwarden CLI..."
    if ! command -v bw &> /dev/null || [ $FORCE_REBUILD -eq 1 ]; then
        if [ $FORCE_REBUILD -eq 1 ]; then
            echo "      - Force update triggered. Downloading latest 'bw'..."
        else
            echo "      - 'bw' not found. Installing..."
        fi
        cd /tmp
        wget -q "https://vault.bitwarden.com/download/?app=cli&platform=linux" -O bw.zip
        unzip -o bw.zip
        sudo mv bw /usr/local/bin/
        sudo chmod +x /usr/local/bin/bw
        rm bw.zip
        echo "      - Bitwarden CLI installed/updated successfully."
    else
        echo "      - 'bw' is already installed at $(which bw)."
    fi

    # --- 4. Virtual Environment ---
    echo "[4/5] Setting up Python Virtual Environment..."
    if [ $FORCE_REBUILD -eq 1 ] && [ -d "$VENV_DIR" ]; then
        echo "      - Force rebuild triggered. Removing existing venv..."
        rm -rf "$VENV_DIR"
    elif [ -d "$VENV_DIR" ] && [ ! -f "$VENV_DIR/bin/activate" ]; then
        echo "      - Found broken venv. Removing..."
        rm -rf "$VENV_DIR"
    fi

    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        echo "      - Created fresh venv at $VENV_DIR"
    else
        echo "      - Valid venv already exists."
    fi

    # --- 5. Python Libraries ---
    echo "[5/5] Syncing Python Requirements..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install --upgrade -r "$PROJECT_DIR/requirements.txt"
    echo "      - Python packages synced."

    echo "========================================"
    echo "   ✅ PROCESS COMPLETE"
    echo "========================================"

} > "$LOG_FILE" 2>&1

echo ""
echo "Process finished. Check log for details."