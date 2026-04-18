#!/bin/bash
set -e # Stop on error

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_DIR="$PROJECT_DIR/venv"

echo "========================================"
echo "   CTRL_S_MASTER: FULL INSTALLATION"
echo "========================================"

# 1. Install System Dependencies & Repositories
echo "[1/5] Installing System Prerequisites..."
sudo apt-get update
# software-properties-common is needed for add-apt-repository
sudo apt-get install -y software-properties-common coreutils python3-venv python3-pip unzip rsync curl dos2unix

# 2. Install VeraCrypt
echo "[2/5] Checking VeraCrypt..."
if ! command -v veracrypt &> /dev/null; then
    echo "      - VeraCrypt not found. Adding PPA..."
    sudo add-apt-repository ppa:unit193/encryption -y
    sudo apt-get update
    sudo apt-get install -y veracrypt
    echo "      - VeraCrypt installed successfully."
else
    echo "      - VeraCrypt is already installed."
fi

# 3. Install Bitwarden CLI
echo "[3/5] Checking Bitwarden CLI..."
if ! command -v bw &> /dev/null; then
    echo "      - 'bw' not found. Installing..."
    cd /tmp
    wget "https://vault.bitwarden.com/download/?app=cli&platform=linux" -O bw.zip
    unzip -o bw.zip
    sudo mv bw /usr/local/bin/
    sudo chmod +x /usr/local/bin/bw
    rm bw.zip
    echo "      - Bitwarden CLI installed successfully."
else
    echo "      - 'bw' is already installed at $(which bw)."
fi

# 4. Create Virtual Environment (Self-Healing)
echo "[4/5] Setting up Python Virtual Environment..."
if [ -d "$VENV_DIR" ] && [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "      - Found broken venv. Removing..."
    rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "      - Created venv at $VENV_DIR"
else
    echo "      - Valid venv already exists."
fi

# 5. Install Python Libraries
echo "[5/5] Installing Python Requirements..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$PROJECT_DIR/requirements.txt"

# 6. Setup Logs Directory
mkdir -p "$PROJECT_DIR/_logs"

echo "========================================"
echo "   ✅ SETUP COMPLETE"
echo "========================================"
