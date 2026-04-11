# ctrl_s_master `windows`
> *Named after the universal shortcut for saving: CTRL + S.*

A robust, automated suite for creating local archives of critical data from services like Bitwarden, and for syncing other key files (like Raindrop.io, Obsidian notes, etc.). The entire process is orchestrated, encrypted, logged, and sends email notifications upon completion or failure.

---

## 🏗️ Architecture

This system uses a **Supervisor & Engine** model designed to keep your data secure at rest.

1.  **The Vault:** A VeraCrypt container (`vaults.hc`) acts as the secure, encrypted destination.
2.  **The Supervisor (`run.bat`):** Handles the physical security. It automatically mounts the encrypted container, establishes secure directory junctions, injects the hidden `.env` secrets, runs the engine, backs up the `.hc` file to your sync folder, and immediately unmounts/locks the container upon completion.
3.  **The Engine (`master_automation.py`):** A Python application that orchestrates API calls, cryptographic conversions (JSON → KDBX), and FreeFileSync operations.

---

## 🚀 Getting Started

Follow these steps to set up the project from scratch on a new machine.

### 1. Install Prerequisites

Make sure you have the following software installed on your system:

*   **VeraCrypt**: Required to create and mount the secure vault. - [Download VeraCrypt](https://www.veracrypt.fr/en/Downloads.html)
*   **Python**: Required for running the core automation scripts. - [Download Python](https://www.python.org/downloads/)
*   **FreeFileSync**: Required for robust file syncing. - [Download FreeFileSync](https://freefilesync.org/download.php)
*   **KeePassXC**: Highly recommended for viewing the final, encrypted `.kdbx` password vaults. - [Download KeePassXC](https://keepassxc.org/download/)

### 2. Prepare the Container & Security (The Auto-Unlocker)

1. **Create the Vault:** Use the VeraCrypt GUI to create a standard volume named `vaults.hc` and place it directly in the root of this project folder (right next to `run.bat`).
2. **Configure the Auto-Unlocker:** The script looks for a plain-text file in your user directory containing your container password. 
   * Open Notepad, type **only** your `vaults.hc` password, and save it as `.vc_secret` inside your `C:\Users\<YourUsername>\` folder.

### 🛡️ Hardening the Secret File (Recommended)
While your user directory is private, you can further secure the `.vc_secret` file using Windows native tools. Open a Command Prompt and run these three commands to hide, lock down, and encrypt the secret:

```cmd
:: 1. Hide it from normal view and system searches
attrib +h +s "%USERPROFILE%\.vc_secret"

:: 2. Restrict access exclusively to your account (and the SYSTEM)
icacls "%USERPROFILE%\.vc_secret" /inheritance:r /grant "%USERNAME%:R" /grant "SYSTEM:F"

:: 3. Encrypt the file content using Windows EFS (tied to your login)
cipher /e "%USERPROFILE%\.vc_secret"
```
*Note: The file remains readable by the `run.bat` script as long as it runs under your user account.*

### 3. Container Initialization

**Critical Step:** You must configure the internal structure of your encrypted container once before automation can run.

1.  **Mount the container manually:** Open VeraCrypt, select `vaults.hc`, and mount it to drive `Z:` using your password.
2.  **Create the required skeleton:** Open the `Z:` drive and create the following empty folders:
    *   `vaults`
    *   `vaults\json`
    *   `vaults\kdbx`
    *   `2fa`
    *   `backups`
3.  **Secure the .env file:** 
    * Copy the provided `.env.example` file and configure it with your secrets (see section below).
    * Move this configured `.env` file **INSIDE** the mounted `Z:` drive. The script will dynamically copy it out and delete it during runs so your credentials never sit unencrypted on your hard drive.
4.  **Unmount:** Unmount the drive from the VeraCrypt GUI.

### 4. Configure `run.bat` Backup Destination

Open `run.bat` in a text editor. Look for the `BACKUP_DEST` variable near the top and change it to the folder where you want the final, updated `vaults.hc` file copied to (e.g., your Syncthing or OneDrive folder).
```bat
set "BACKUP_DEST=C:\path\to\your\sync\folder"
```

### 5. Run the One-Time Python Setup

This script prepares the python environment. It creates the virtual environment (`venv`), installs all required packages from `requirements.txt`, and downloads the necessary command-line tools.

Open Command Prompt in the project folder and run:
```cmd
setup.bat
```

---

## ⚙️ Configuration (.env)

The `.env` file (now stored safely inside your vault) controls the logic. Follow the guides below to obtain the necessary credentials to fill it out.

```
⚠️ Security Warning
This file handles extremely sensitive information. By keeping it inside the VeraCrypt container, it remains encrypted at rest.
```

##### **A. Getting Bitwarden Credentials**

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

##### **B. Getting Raindrop.io API Tokens**

1.  Log in to your Raindrop.io account.
2.  Go to **Settings** and navigate to the **Integrations** tab.
3.  Under **For Developers** click on **+ Create new app**.
4.  Give the app a name (e.g., "Local Backup Script") and click **Create**.
5.  On the next screen, click the **Create test token** button.
6.  **Copy the generated token.** This is the value for `RAINDROP_PERSONAL_API_TOKEN` or `RAINDROP_WORK_API_TOKEN`.

##### **C. Getting a Google App Password for Email**

To send email notifications using a Gmail account, you must use an App Password.

1.  Ensure **2-Step Verification** is enabled on your Google Account.
2.  Go directly to the App Passwords page: [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3.  Type any name you want to identify it with (e.g., Automation Server).
4.  **Copy the 16-character password** shown in the yellow box (without spaces). This is the value for `EMAIL_PASSWORD`.

##### **D. Configuring Other Essential Paths and Passwords**

*   `BITWARDEN_PERSONAL_PASSWORD` / `BITWARDEN_WORK_PASSWORD`: **Choose a new password** for the `encrypted_json` export files.
*   `KDBX_PERSONAL_PASSWORD` / `KDBX_WORK_PASSWORD`: **Choose a new password** for the final `.kdbx` KeePass files.
*   `FFS_PATH`: Provide the full, absolute path to the FreeFileSync executable (e.g., `C:\Program Files\FreeFileSync\FreeFileSync.exe`).

---

## 🕹️ Usage and Maintenance

### Running the Main Automation

This is the primary command for your scheduled tasks. It mounts the vault, runs the backups, updates your sync folder with the new container, and generates an email report.

```cmd
run.bat
```

For continuous automation, use **Windows Task Scheduler** to run this script on a regular basis (e.g., weekly).

### Updating Dependencies

To update everything (Python packages, Bitwarden CLI) to the latest versions, manually run:

```cmd
update.bat
```

### Testing the Suite

This project includes a professional test suite using `pytest` to ensure its core logic is working. To run it:

```cmd
tests.bat
```

#### Advanced Usage: Stateful Dry Run

For debugging, you can perform a **stateful dry run**. This simulates a full run in a temporary directory that is automatically deleted at the end without touching your real VeraCrypt container.

```cmd
.\venv\Scripts\activate
python src\master_automation.py run-tasks run-all --dry-run
```

---

## 🔓 Accessing Data (Manual Mount)

If you need to retrieve a file from the vault manually:

1. Open the **VeraCrypt** GUI.
2. Click **Select File...** and choose `vaults.hc` from your project folder.
3. Select an available drive letter (e.g., `Z:`).
4. Click **Mount**, enter your password, and browse your files.
5. **Critical:** When finished, select the drive in VeraCrypt and click **Dismount**.

---

## 📂 Project Structure

```
ctrl_s_master/
│
├── 🔒 vaults.hc                          # The Encrypted Container (AES-256).
├── 📜 README.md                          # This file: Project overview, setup, and usage.
├── 🔑 .env.example                       # Template for all configuration variables.
├── 📦 requirements.txt                   # Python dependencies.
│
├── ⚙️ setup.bat                          # One-time script to build the entire environment.
├── 🔄 update.bat                         # Updates all dependencies.
├── ▶️ run.bat                            # The Supervisor: Handles mounting, running, and rotating.
├── 🧪 tests.bat                          # Launcher for the automated test suite.
│
├── 📁 src/                               # Core Application Code.
│   ├── 🐍 master_automation.py           # The Engine.
│   └── 📁 _tools/                        # Helper scripts (Bitwarden, Raindrop, FreeFileSync).
│
├── 📁 _tests/                            # Pytest suite.
├── 📁 _logs/                             # Execution logs (attached to Email).
├── 📁 venv/                              # Isolated Python Environment.
│
├── 📊 status.json                        # (Gitignored) Machine-readable history.
├── 📄 status_dashboard.md                # (Gitignored) Human-readable dashboard.
│
├── 🔗 vaults/                            # [JUNCTION] Created only during runtime.
├── 🔗 2fa/                               # [JUNCTION] Created only during runtime.
├── 🔗 backups/                           # [JUNCTION] Created only during runtime.
├── 📄 .env                               # [COPIED] Injected from vault during runtime only.
│
└── C:\Users\<User>\.vc_secret            # [EXTERNAL] Secure Keyfile for auto-unlock.
```

---

### Author
This project was created and is maintained by myself - [gravi-ctrl](https://github.com/gravi-ctrl).