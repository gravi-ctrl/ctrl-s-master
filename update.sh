#!/bin/bash
set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_DIR="$PROJECT_DIR/venv"
LOG_FILE="$PROJECT_DIR/_logs/update_$(date +%Y%m%d).log"

echo "Starting Update... Logs at $LOG_FILE"
{
    echo "========================================"
    echo "   CTRL_S_MASTER: UPDATE ROUTINE"
    echo "   DATE: $(date)"
    echo "========================================"

    # 1. Update Python Packages
    echo "[1/2] Updating Python Dependencies..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install --upgrade -r "$PROJECT_DIR/requirements.txt"
    echo "      - Python packages updated."

    # 2. Update Bitwarden CLI (Force Re-install)
    echo "[2/2] Updating Bitwarden CLI..."
    cd /tmp
    # Download latest
    wget -q "https://vault.bitwarden.com/download/?app=cli&platform=linux" -O bw.zip
    unzip -o bw.zip
    # Overwrite existing binary
    sudo mv bw /usr/local/bin/
    sudo chmod +x /usr/local/bin/bw
    rm bw.zip
    echo "      - Bitwarden CLI updated to latest version."

    echo "========================================"
    echo "   ✅ UPDATE COMPLETE"
    echo "========================================"

} | tee -a "$LOG_FILE"