# ctrl_s_master `linux`
> *Named after the universal shortcut for saving: CTRL + S.*

A robust, automated orchestration engine for self-hosted disaster recovery. This suite manages the retrieval, encryption, and backup of critical digital assets (Bitwarden, Raindrop, 2FA, Documentation) into a localized, encrypted VeraCrypt container on a headless Linux server.

---

## 🏗️ Architecture

This system uses a **Supervisor & Engine** model designed for headless environments.

1.  **The Vault:** A VeraCrypt container (`vaults.hc`) acts as the secure destination.
2.  **The Supervisor (`run.sh`):** Handles the "Air Gap." It mounts the encrypted container, injects secrets via symlinks, executes the logic, and immediately unmounts/locks the container upon completion.
3.  **The Engine (`master_automation.py`):** A Python application that orchestrates API calls, cryptographic conversions (JSON → KDBX), and Rsync operations.

---

## 🚀 Installation & Setup

Follow these steps to set up the project on a fresh Ubuntu/Debian server.

### 1. Prerequisites
*   **OS:** Ubuntu Server (LTS recommended) or Debian.
*   **Permissions:** Root/Sudo access is required for mounting drives.
*   **Data:** You must have a VeraCrypt container file ready (`vaults.hc`).

### 2. One-Time Setup
Copy the project folder to your server (e.g., `/home/gravi-ctrl/scripts/ctrl_s_master/linux`), then run the installer. This script installs system dependencies (VeraCrypt, Bitwarden CLI, Python) and creates the virtual environment.

```bash
chmod +x setup.sh
./setup.sh
```

### 3. Security Configuration (The Auto-Unlocker)
Since the server is headless, we use a keyfile restricted to the root user to unlock the vault automatically.

```bash
# Replace 'YOUR_REAL_PASSWORD' with the actual password for vaults.hc
sudo sh -c 'echo "YOUR_REAL_PASSWORD" > /root/.vc_secret'
sudo chmod 600 /root/.vc_secret
```

### 4. Container Initialization
**Critical Step:** You must configure the internal structure of your encrypted container once before automation can run.

1.  **Mount the container manually:**
    ```bash
    sudo mkdir -p /mnt/secure_vaults
    sudo veracrypt --text --pim=0 --keyfiles="" --protect-hidden=no /home/gravi-ctrl/scripts/ctrl_s_master/linux/vaults.hc /mnt/secure_vaults
    ```
2.  **Create the required skeleton:**
    ```bash
    cd /mnt/secure_vaults
    sudo mkdir -p vaults/json vaults/kdbx 2fa backups
    ```
3.  **Secure the .env file:**
    Move your configured `.env` file **INSIDE** the container. The script will symlink it dynamically during runs.
    ```bash
    sudo mv /home/gravi-ctrl/scripts/ctrl_s_master/linux/.env /mnt/secure_vaults/.env
    ```
4.  **Set Permissions & Unmount:**
    ```bash
    sudo chown -R $(id -u):$(id -g) /mnt/secure_vaults
    sudo veracrypt --text --dismount /home/gravi-ctrl/scripts/ctrl_s_master/linux/vaults.hc
    ```

---

## ⚙️ Configuration (.env)

The `.env` file (now stored inside the vault) controls the logic. Rename `.env.example` to `.env` and configure it using the guides below.

### 🔑 Obtaining Secrets

#### **A. Getting Bitwarden Credentials**

The Bitwarden export process requires an `Access Token` and several `UUIDs` from Secrets Manager. Follow these steps to get them:

1.  **Create a Machine Account & Access Token:** This token allows the script to securely access your other secrets.
    *   Navigate to **Bitwarden Secrets Manager** → **Machine accounts**.
    *   Create a new machine account if you don't have one for this script.
    *   Under **Access tokens**, click **+Create access token** and give it a name (e.g., `vault-exporter`).
    *   **Copy the generated Access Token immediately as you won't see it again.** This is the value for `BW_ACCESS_TOKEN`.

2.  **Get Your Vault's API Key:** You will need a unique API key for each Bitwarden vault you intend to export.
    *   Navigate to your **Bitwarden Web Vault** → **Settings** → **Security** → **Keys**.
    *   Click **View API Key** to get your `client_id` and `client_secret`.

3.  **Create Secrets, Grant Access, and Copy UUIDs:**
    *   In Secrets Manager, create a new **Project** to hold your credentials.
    *   Inside the project, create three new secrets containing your: `client_id`, `client_secret`, and `Master Password`.
    *   In the project’s **People** tab, add your user account and grant it **Read** access. **This is a critical step.**
    *   Click each secret, then **Machine accounts**, and choose the Machine account with the Access token inside.
        *   Copy each secret's **ID (UUID)**. These are the values for the `BW_*_CLIENT_ID_UUID`, `BW_*_CLIENT_SECRET_UUID`, and `BW_*_MASTER_PASSWORD_UUID` variables.

4.  **Find Your Organization ID (If Needed):** This is only required if `BW_*_IS_ORGANIZATION` is set to `true`.
    *   In the Web Vault, open the **Admin Console** and select your Organization.
    *   The Organization ID is in your browser's URL, between `/organizations/` and `/vault`.

#### **B. Getting Raindrop.io API Tokens**

You will need a separate API token for each Raindrop.io account you want to back up.

1.  Log in to your Raindrop.io account.
2.  Go to **Settings** and navigate to the **Integrations** tab.
3.  Under **For Developers** click on **+ Create new app**.
4.  Give the app a name (e.g., "Local Backup Script") and click **Create**.
5.  On the next screen, click the **Create test token** button.
6.  **Copy the generated token.** This is the value for `RAINDROP_PERSONAL_API_TOKEN` or `RAINDROP_WORK_API_TOKEN`. Repeat for your other account if necessary.

#### **C. Getting Telegram Bot Credentials**

To send report notifications via Telegram, you need a Bot Token and your numeric Chat ID.

1.  **Create the Bot:**
    *   Open Telegram and search for **@BotFather**.
    *   Send the command `/newbot`.
    *   Follow the prompts to name your bot.
    *   **Copy the API Token** provided (e.g., `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`). This is the value for `TELEGRAM_BOT_TOKEN`.

2.  **Get Your Chat ID:**
    *   Start a chat with your new bot (click the link provided by BotFather and press Start).
    *   Search for **@userinfobot** (or similar ID bots) and start it.
    *   It will reply with your "Id".
    *   **Copy this number.** This is the value for `TELEGRAM_CHAT_ID`.

---

## 🕹️ Usage

### Manual Run
To trigger the full backup cycle manually:
```bash
sudo ./run.sh
```
*This will mount the container, run all tasks, send the Telegram report, and unmount.*

### Dry Run
To trigger a dry run:
```bash
sudo ./run.sh dry
```

### Maintenance
To update Python dependencies and the Bitwarden CLI binary:
```bash
sudo ./update.sh
```

### Run Tests
To verify the Python logic without touching real data (Dry Run):
```bash
./tests.sh
```

---

## 🔓 Accessing Data (Manual Mount)

If you need to retrieve a file from the vault manually, you must mount it to a temporary folder.

```bash
# 1. Create Mount Point (If not exists)
sudo mkdir -p /mnt/secure_vaults

# 2. Mount (Will ask for password if .vc_secret is missing)
sudo veracrypt --text --pim=0 --keyfiles="" --protect-hidden=no /home/gravi-ctrl/scripts/ctrl_s_master/linux/vaults.hc /mnt/secure_vaults

# 3. Explore
ls -lh /mnt/secure_vaults/

# 4. Unmount (Critical!)
sudo veracrypt --text --dismount /home/gravi-ctrl/scripts/ctrl_s_master/linux/vaults.hc
```

---

## ⏰ Automation (Cron)

This suite is designed to run bi-weekly (2nd and 4th Friday of the month).

**Add to Root Crontab (`sudo crontab -e`):**
```bash
0 4 8-14,22-28 * * [ "$(date +\%u)" = 5 ] && /home/gravi-ctrl/scripts/ctrl_s_master/linux/run.sh
```

---

## 📂 Project Structure

```
ctrl_s_master/
│
├── 🔒 vaults.hc                   # The Encrypted Container (AES-256).
├── 📄 requirements.txt            # Python dependencies.
│
├── 📜 setup.sh                    # One-time installer (System + Venv).
├── 📜 update.sh                   # Updates Python libs and Bitwarden CLI.
├── 📜 run.sh                      # The Supervisor. Handles mounting/unmounting.
├── 📜 tests.sh                    # Test suite launcher.
│
├── 📁 src/                        # Core Application Code.
│   ├── 🐍 master_automation.py    # The Engine.
│   └── 📁 _tools/                 # Helper scripts (Bitwarden, Raindrop, Crypto).
│
├── 📁 _tests/                     # Pytest suite.
├── 📁 _logs/                      # Execution logs (attached to Telegram).
├── 📁 venv/                       # Isolated Python Environment.
│
├── 🔗 vaults/                     # [SYMLINK] Created only during runtime.
├── 🔗 2fa/                        # [SYMLINK] Created only during runtime.
├── 🔗 backups/                    # [SYMLINK] Created only during runtime.
│
└── /root/.vc_secret               # [EXTERNAL] Secure Keyfile (Read-only by Root).
```

---

### Author
This project was created and is maintained by myself - [gravi-ctrl](https://github.com/gravi-ctrl).
