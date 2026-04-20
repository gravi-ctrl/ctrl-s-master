# ctrl_s_master
> *Named after the universal shortcut for saving: CTRL + S.*

A robust, automated suite for creating local archives of critical data from services like Bitwarden and Raindrop.io, and for syncing other key files (2FA codes, documentation, etc.). The entire process is orchestrated, encrypted, logged, and sends a notification upon completion or failure.

The project runs on **both Windows and Linux** from a single shared codebase.

---

## 🏗️ Architecture

This system uses a **Supervisor & Engine** model designed to keep your data secure at rest.

1. **The Vault:** A VeraCrypt container (`vaults.hc`) acts as the secure, encrypted destination for all outputs.
2. **The Supervisor (`run.bat` / `run.sh`):** Handles the physical security layer. It mounts the encrypted container, establishes secure directory links, injects the hidden `.env` secrets, runs the engine, backs up the `.hc` file to your sync folder, and immediately unmounts/locks the container upon completion.
3. **The Engine (`master_automation.py`):** A Python application that orchestrates API calls, cryptographic conversions (JSON → KDBX), and file sync operations.

| Layer | Windows | Linux |
| :--- | :--- | :--- |
| Supervisor | `run.bat` | `run.sh` |
| Directory Links | NTFS Junctions (`mklink /J`) | Symlinks (`ln -sfn`) |
| File Sync | FreeFileSync (`.ffs_batch` files) | rsync (`.json` files) |
| Notifications | Email (SMTP) | Telegram |
| Fail-Safe | Pre-run cleanup & GOTO traps | Native `trap` signals |

> Both channels can be active simultaneously if both are configured in `.env`.

---

## 🚀 Getting Started

### Step 1 — Install Prerequisites

#### 🪟 Windows
| Software | Purpose | Link |
| :--- | :--- | :--- |
| Python | Core runtime | [python.org](https://www.python.org/downloads/) |
| VeraCrypt | Encrypted container | [veracrypt.fr](https://www.veracrypt.fr/en/Downloads.html) |
| FreeFileSync | File syncing | [freefilesync.org](https://freefilesync.org/download.php) |
| KeePassXC | Viewing `.kdbx` vaults | [keepassxc.org](https://keepassxc.org/download/) |

#### 🐧 Linux (Ubuntu/Debian)
- Root/sudo access is required for mounting.
- All system dependencies (`veracrypt`, `bitwarden CLI`, `python-venv`, `rsync`) are installed automatically by `setup.sh`.

---

### Step 2 — One-Time Environment Setup

#### 🪟 Windows
Open a Command Prompt in the project folder and run:
```cmd
setup.bat
```
This creates the Python virtual environment, installs all packages from `requirements.txt`, and downloads the Bitwarden CLI binary into `src/_tools/bw/`.

#### 🐧 Linux
```bash
chmod +x setup.sh
./setup.sh
```
This installs system packages, VeraCrypt (via PPA), the Bitwarden CLI, and creates the Python virtual environment.

---

### Step 3 — Configure the Auto-Unlocker

The supervisor scripts need a way to open the VeraCrypt container without interactive input. Both platforms use a plain-text keyfile stored in your user directory.

#### 🪟 Windows
Create a file named `.vc_secret` in `C:\Users\<YourUsername>\` containing **only** your vault password. Then harden it:
```cmd
:: 1. Hide the file from normal view
attrib +h +s "%USERPROFILE%\.vc_secret"

:: 2. Restrict access to your account only
icacls "%USERPROFILE%\.vc_secret" /inheritance:r /grant "%USERNAME%:R" /grant "SYSTEM:F"

:: 3. Encrypt using Windows EFS (tied to your login)
cipher /e "%USERPROFILE%\.vc_secret"
```
*The file remains readable by `run.bat` as long as it runs under your user account.*

#### 🐧 Linux
```bash
# Paste your vaults.hc password inside the .vc_secret
sudo nano /root/.vc_secret
sudo chmod 600 /root/.vc_secret
```
The file is owned and readable only by root, which is the same user that runs the supervisor.

---

### Step 4 — Initialize the Container

This is a one-time step. You must create the required folder skeleton inside the container before the first automated run.

#### 🪟 Windows
1. Open the VeraCrypt GUI, select `vaults.hc`, and mount it to drive **`Z:`**.
2. Open the `Z:` drive and create these folders:
   ```
   Z:\vaults\
   Z:\vaults\json\
   Z:\vaults\kdbx\
   Z:\2fa\
   Z:\backups\
   ```
3. Copy your configured `.env` file **onto the `Z:` drive** (see Configuration section below). The supervisor copies it out at runtime and deletes it when done — your credentials never sit unencrypted on disk.
4. Unmount from the VeraCrypt GUI.

#### 🐧 Linux
```bash
# 1. Mount manually
sudo mkdir -p /mnt/secure_vaults
sudo veracrypt --text --pim=0 --keyfiles="" --protect-hidden=no ./vaults.hc /mnt/secure_vaults

# 2. Create the skeleton
sudo mkdir -p /mnt/secure_vaults/vaults/json \
              /mnt/secure_vaults/vaults/kdbx \
              /mnt/secure_vaults/2fa \
              /mnt/secure_vaults/backups

# 3. Move your configured .env inside (symlinked in at runtime)
sudo mv .env /mnt/secure_vaults/.env

# 4. Fix ownership and unmount
sudo chown -R $(id -u):$(id -g) /mnt/secure_vaults
sudo veracrypt --text --dismount ./vaults.hc
```

---

### Step 5 — Configure the Backup Destination

Open your platform's supervisor script and set the `BACKUP_DEST` variable to the folder where the updated `vaults.hc` should be copied after each successful run (e.g., your Syncthing or cloud sync folder).

```bat
:: run.bat
set "BACKUP_DEST=D:\x\@Sync\My_Shit"
```
```bash
# run.sh
BACKUP_DEST="/data/assets/syncthing/My_Shit"
```

---

## ⚙️ Sync Job Configuration

File syncing is fully dynamic and data-driven. **No code changes are required to add new sync jobs.**

#### 🪟 Windows (FreeFileSync)
Drop any `.ffs_batch` file into `src/_tools/ffs_jobs/`. The engine will automatically discover and run it. Ensure `FFS_PATH` is correctly set in your `.env` file so the engine knows where the FreeFileSync executable is located.

*Note: Use %AUTOMATION_ROOT% in your FreeFileSync batch file paths to refer dynamically to the project directory.*

#### 🐧 Linux (rsync)
Drop a `.json` file into `src/_tools/rsync_jobs/`. The engine will automatically parse the file and execute the `rsync` command. 

Example `sync_backups.json`:
```json
{
    "source": "/home/user/backups",
    "dest": "${AUTOMATION_ROOT}/backups",
    "excludes":["_pvt", ".stfolder", ".stversions", ".git"]
}
```

*Note: Use ${AUTOMATION_ROOT} in your JSON configuration paths to refer dynamically to the project directory.*

---

## 🔐 Configuration (`.env`)

The `.env` file lives inside the VeraCrypt container and is the single source of truth for all credentials and paths. Use `.env.example` as your starting template.

> ⚠️ **Security Warning:** This file handles extremely sensitive information. Keeping it inside the container ensures it is always encrypted at rest and only exposed in memory during an active run.

### Obtaining Secrets

#### A. Bitwarden Credentials

The export process uses Bitwarden Secrets Manager to retrieve credentials at runtime — your passwords never sit in a config file.

1. **Create a Machine Account & Access Token**
   - Navigate to **Bitwarden Secrets Manager** → **Machine accounts**.
   - Create a new machine account (e.g., `vault-exporter`).
   - Under **Access tokens**, click **+ Create access token**.
   - **Copy the token immediately** — you won't see it again. This is `BW_ACCESS_TOKEN`.

2. **Get Your Vault's API Key**
   - Go to **Bitwarden Web Vault** → **Settings** → **Security** → **Keys**.
   - Click **View API Key** to get your `client_id` and `client_secret`.
   - Repeat for each vault (personal, work) you want to export.

3. **Create Secrets and Copy UUIDs**
   - In Secrets Manager, create a **Project** and add three secrets per vault: `client_id`, `client_secret`, and `Master Password`.
   - In the project's **People** tab, grant your user account **Read** access — this is a required step.
   - Link the machine account to each secret via the **Machine accounts** tab.
   - Copy each secret's **UUID**. These fill `BW_*_CLIENT_ID_UUID`, `BW_*_CLIENT_SECRET_UUID`, and `BW_*_MASTER_PASSWORD_UUID`.

4. **Organization ID** *(only if `BW_*_IS_ORGANIZATION=true`)*
   - In the Web Vault **Admin Console**, select your Organization.
   - The ID is in the browser URL between `/organizations/` and `/vault`.

#### B. Raindrop.io API Tokens

Repeat for each account (personal, work) you want to back up.

1. Log in at [raindrop.io](https://raindrop.io) → **Settings** → **Integrations**.
2. Under **For Developers**, click **+ Create new app** and give it a name.
3. Click **Create test token** and copy it.
   - Personal token → `RAINDROP_PERSONAL_API_TOKEN`
   - Work token → `RAINDROP_WORK_API_TOKEN`

#### C. Notification Credentials

Configure one or both channels. If both are set in `.env`, both will fire.

##### 🪟 Email (default on Windows, optional on Linux)
Use a Gmail App Password to avoid storing your real account password.

1. Ensure **2-Step Verification** is enabled on your Google account.
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Create a new App Password (name it anything, e.g., `Automation`).
4. Copy the 16-character password (no spaces). This is `EMAIL_PASSWORD`.

```env
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=465
EMAIL_SENDER=your_email@gmail.com
EMAIL_PASSWORD=your-16-char-app-password
EMAIL_RECIPIENT=recipient@example.com
```

##### 🐧 Telegram (default on Linux, optional on Windows)

1. Open Telegram and message **@BotFather** → `/newbot` → follow the prompts.
2. Copy the API token provided. This is `TELEGRAM_BOT_TOKEN`.
3. Start a chat with your new bot, then message **@userinfobot** to get your numeric user ID. This is `TELEGRAM_CHAT_ID`.

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=987654321
```

#### D. Other Passwords & Paths

| Variable | Purpose |
| :--- | :--- |
| `BITWARDEN_*_PASSWORD` | Password to encrypt the intermediate JSON export files. Choose anything — this is not your Bitwarden master password. |
| `KDBX_*_PASSWORD` | Password you'll use to open the final `.kdbx` file in KeePassXC. |
| `FFS_PATH` | **Windows only.** Path to `FreeFileSync.exe`. Can be relative to project root or absolute. |
| `BW_CLI_PATH` | `src/_tools/bw/bw.exe` on Windows. Set to `bw` on Linux if installed globally. |

---

## 🔐 Security & Cleanup

To prevent API tokens and secret metadata from leaking to your physical disk via unallocated space, the Supervisor scripts perform a **Secure Wipe** on every exit:

*   **Linux:** Uses `shred -u` to overwrite the temporary `.env` handoff file 3 times with random data before deletion.
*   **Windows:** Overwrites the temporary `.env` file with dummy data before calling `del`.
*   **Links:** Junctions and Symlinks are removed instantly on exit, leaving no path for a threat actor to reach the encrypted data.

---

## 🕹️ Usage

### Running the Automation

#### 🪟 Windows
```cmd
run.bat
```
For scheduling, use **Windows Task Scheduler** to run `run.bat` on a regular basis (e.g., weekly).

#### 🐧 Linux
```bash
sudo ./run.sh
```

For scheduling, add to the root crontab (`sudo crontab -e`). Example for bi-weekly runs on the 2nd and 4th Friday:
```bash
0 4 8-14,22-28 * *[ "$(date +\%u)" = 5 ] && /path/to/ctrl_s_master/run.sh
```

*Note: The status dashboard and notification reports are "Config-Aware." If a vault (Work or Personal) is not configured in your .env, the task will be automatically skipped and hidden from the report to keep your dashboard clean.*

---

### Dry Run

Simulates a full run in a temporary directory without touching the real container or sending notifications. Useful for debugging.

#### 🪟 Windows
```cmd
:: Run everything
python src\master_automation.py run-tasks run-all --dry-run

:: Run a specific vault only (e.g., Personal Bitwarden)
python src\master_automation.py run-tasks export-personal --dry-run
```

#### 🐧 Linux
```bash
# Run everything
sudo ./run.sh dry

# Run a specific vault only (e.g., Work Raindrop)
source venv/bin/activate
python3 src/master_automation.py run-tasks raindrop-work --dry-run
```

---

### Updating Dependencies

To update your environment, download the latest CLI tools, or repair a broken setup, run the setup script with the force flag:

#### 🪟 Windows
```cmd
setup.bat /f
```

#### 🐧 Linux
```bash
sudo ./setup.sh -f
```

---

### Running Tests

#### 🪟 Windows
```cmd
tests.bat
```

#### 🐧 Linux
```bash
./tests.sh
```

---

## 🔓 Accessing Data (Manual Mount)

If you need to retrieve a file from the vault outside of an automated run.

#### 🪟 Windows
1. Open the **VeraCrypt** GUI.
2. Click **Select File...** and choose `vaults.hc`.
3. Select an available drive letter (e.g., `Z:`).
4. Click **Mount** and enter your password.
5. Browse your files, then **Dismount** when done.

#### 🐧 Linux
```bash
# Mount
sudo mkdir -p /mnt/secure_vaults
sudo veracrypt --text --pim=0 --keyfiles="" --protect-hidden=no ./vaults.hc /mnt/secure_vaults

# Explore
ls -lh /mnt/secure_vaults/

# Unmount (critical!)
sudo veracrypt --text --dismount ./vaults.hc
```

---

## ➕ Adding New Sync Folders

To add a new encrypted sync destination (e.g., `another_bak`), follow these three steps:

### 1. Update the Container
Mount `vaults.hc` manually and create the new folder inside:
- `Z:\another_bak\` (Windows) or `/mnt/secure_vaults/another_bak/` (Linux)

### 2. Update the Supervisor Scripts
Add the folder name to the `SECURE_FOLDERS` list at the top of your supervisor scripts. The scripts will handle the creation and removal of the links automatically.

- **Windows (`run.bat`):**  
  `set "SECURE_FOLDERS=vaults 2fa backups another_bak"`

- **Linux (`run.sh`):**  
  `SECURE_FOLDERS=("vaults" "2fa" "backups" "another_bak")`

### 3. Create the Sync Job
Drop your `.ffs_batch` (Windows) or `.json` (Linux) file into the respective `_tools` folder. Ensure the destination path in your job points to the newly linked folder within the project directory.

---

## 📂 Project Structure

```
ctrl_s_master/
│
├── 🔒 vaults.hc                          # The Encrypted Container (AES-256).
├── 📜 README.md                          # This file.
├── 🔑 .env.example                       # Configuration template.
├── 📦 requirements.txt                   # Python dependencies.
│
├── ▶️  run.bat / run.sh                  # Supervisor: mount, run, backup, unmount.
├── ⚙️  setup.bat / setup.sh              # Setup & Update environment.
├── 🧪 tests.bat / tests.sh              # Test suite launcher.
│
├── 📁 src/
│   ├── 🐍 master_automation.py           # The Engine: orchestrates all tasks.
│   └── 📁 _tools/
│       ├── 📁 ffs_jobs/                  # Drop Windows FreeFileSync batch jobs here.
│       ├── 📁 rsync_jobs/                # Drop Linux rsync JSON config files here.
│       ├── bitwarden_exporter.py         # Exports Bitwarden vaults via API.
│       ├── convert-to-kdbx.py            # Decrypts JSON exports → KeePass .kdbx.
│       ├── raindrop_backup.py            # Downloads and archives Raindrop bookmarks.
│       └── common_utils.py               # Shared helpers (backup rotation, etc.).
│
├── 📁 _tests/                            # Pytest suite.
├── 📁 _logs/                             # Timestamped execution logs.
├── 📁 venv/                              # Isolated Python environment.
│
├── 📊 status.json                        # (Gitignored) Machine-readable run history.
├── 📄 status_dashboard.md                # (Gitignored) Human-readable run dashboard.
│
│   — Created at runtime, removed after —
├── 🔗 vaults/                            # Junction (Win) / Symlink (Lin) into container.
├── 🔗 2fa/                               # Junction (Win) / Symlink (Lin) into container.
├── 🔗 backups/                           # Junction (Win) / Symlink (Lin) into container.
├── 📄 .env                               # Injected from vault, deleted post-run.
│
│   — External —
└── 🔑 ~/.vc_secret                       # Auto-unlocker keyfile (user home on both OSes).
```
	
---
	
### Author
This project was created and is maintained by myself - [gravi-ctrl](https://github.com/gravi-ctrl).