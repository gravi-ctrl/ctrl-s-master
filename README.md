![License](https://img.shields.io/badge/license-MIT-green?label=License)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-blue?label=Platform)
[![Code Quality & Tests](https://github.com/gravi-ctrl/ctrl-s-master/actions/workflows/ci.yml/badge.svg)](https://github.com/gravi-ctrl/ctrl-s-master/actions/workflows/ci.yml)

# ctrl-s-master
*Named after the universal shortcut for saving: CTRL + S.*

This is my safety net for the accounts I can't afford to lose access to. It makes local, encrypted copies of my Bitwarden vaults and Raindrop bookmarks, syncs a few other key folders (2FA backups, documentation), and keeps all of it inside a container that is only open while the job runs. When something goes wrong, I get a message.

It runs on **both Windows and Linux** from a single codebase.

### ✨ The Short Version

* **Encrypted at rest.** Everything lands in a VeraCrypt container (`vaults.hc`). It's mounted for the length of a run and locked again right after.
* **Secrets stay in the container.** The `.env` lives inside the encrypted container and is loaded into the run's memory, never copied into the project folder.
* **Backed up twice.** After a successful run the container is copied to my sync folder (one dated copy, replacing the previous one) and optionally uploaded to Backblaze B2, which keeps several dated copies.
* **New sync jobs are files, not code.** Drop a FreeFileSync batch (Windows) or an rsync JSON file (Linux) into the jobs folder and the next run picks it up.
* **It tells me when it breaks.** A report goes out after every run (Telegram, email, or both). Even a failure before the container opens sends a Telegram alert, through a tiny separate keyfile.
* **Tested on both platforms.** I've run complete backups on Windows and on Linux, including the local copy and the B2 upload.
* **Built to be recovered without it.** `breakglass/` holds standalone scripts that read every export without needing this project.

---

## 🏗️ How it Works

Two scripts split the job, so the part that handles secrets stays small:

1. **The Vault:** `vaults.hc`, a VeraCrypt container. It's the only place the outputs live.
2. **The Supervisor** (`run.bat` / `run.sh`): the security layer. It mounts the container, links its folders into the project, loads the secrets into memory, hands over to the Engine, copies the container to my sync folder and to B2, and unmounts it again.
3. **The Engine** (`master_automation.py`): the Python part that does the actual work, which is the Bitwarden and Raindrop exports and the file syncs.

| Layer | Windows | Linux |
| :--- | :--- | :--- |
| Supervisor | `run.bat` | `run.sh` |
| Local Backup | Copy to `BACKUP_DEST` | Copy to `BACKUP_DEST` |
| Offsite Backup | Backblaze B2 (`rclone` in RAM) | Backblaze B2 (`rclone` in RAM) |
| Directory Links | NTFS Junctions (`mklink /J`) | Symlinks (`ln -sfn`) |
| File Sync | FreeFileSync (`.ffs_batch` files) | rsync (`.json` files) |
| Fail-Safe | Pre-run cleanup & GOTO traps | Native `trap` signals |
| Notifications | Email (SMTP) | Telegram |

> Both notification channels can be active at once if both are configured in `.env`.

### A run, step by step

1. **Clean up** anything a previous crash left behind (stale links, a container that is still mounted).
2. **Mount** `vaults.hc`, using the password from the `.vc_secret` keyfile.
3. **Link** the container's folders (`vaults`, `2fa`, `backups`) into the project.
4. **Load** the container's `.env` into memory.
5. **Run the Engine:** the Raindrop backups, then the Bitwarden exports, then any sync jobs.
6. **Remove the links and dismount** the container.
7. **Back up the container,** but only if every task succeeded: copy it to the sync folder, then upload it to B2 if that's configured.
8. **Send the report.**

If a task fails, the run stops (unless `CONTINUE_ON_ERROR=true`), the container is still dismounted, the container backups are skipped, and a failure report goes out.

---

## 🚀 Getting Started

### Step 1: Install Prerequisites

#### 🪟 Windows
| Software | Purpose | Link |
| :--- | :--- | :--- |
| Python | Core runtime (must be on your `PATH`) | [python.org](https://www.python.org/downloads/) |
| VeraCrypt | Encrypted container | [veracrypt.fr](https://www.veracrypt.fr/en/Downloads.html) |
| FreeFileSync | File syncing | [freefilesync.org](https://freefilesync.org/download.php) |
| Chocolatey | Package manager that `setup.bat` uses to install the tools below | [chocolatey.org](https://chocolatey.org/install) |
| Bitwarden CLI | Vault exports | Installed via `setup.bat` |
| rclone | Cloud Backup | Installed via `setup.bat` |

#### 🐧 Linux (Ubuntu/Debian)
- Root/sudo access is required for mounting.
- `setup.sh` installs everything else: VeraCrypt (from a PPA), the Bitwarden CLI, rclone, rsync, curl, unzip, dos2unix and the Python venv tooling.

---

### Step 2: One-Time Environment Setup

#### 🪟 Windows
1. Open a **Command Prompt as Administrator**.
2. Navigate to the project folder and run:
```cmd
setup.bat
```
*This builds the Python virtual environment, installs the Python requirements, and uses Chocolatey to install or update the Bitwarden CLI and rclone system-wide.*

#### 🐧 Linux
```bash
chmod +x setup.sh
sudo ./setup.sh
```
This installs the system packages, VeraCrypt (via PPA), the Bitwarden CLI and rclone, then creates the Python virtual environment and installs the requirements.

---

### Step 3: Configure the Auto-Unlocker

The supervisor scripts need a way to open the VeraCrypt container without interactive input. Both platforms use a plain-text keyfile stored in your user directory.

#### 🪟 Windows
Create a file named `.vc_secret` in `C:\Users\<YourUsername>\` containing **only** your vault password (one line). Then harden it:
```cmd
:: 1. Encrypt using Windows EFS (tied to your login)
cipher /e "%USERPROFILE%\.vc_secret"

:: 2. Hide the file from normal view
attrib +h +s "%USERPROFILE%\.vc_secret"

:: 3. Restrict access to your account only
icacls "%USERPROFILE%\.vc_secret" /inheritance:r /grant "%USERNAME%:R" /grant "SYSTEM:F"
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

### Step 3b: Configure Fatal Notifications (Recommended)

The normal Telegram or email report needs secrets from the container to fire. If a run fails *before* the container is loaded (wrong password, missing container, a Python error while reading `.env`), you would get no notification at all and would only find out by checking the logs.

To close that gap, create a second minimal keyfile **outside the container** containing only your Telegram bot credentials. The supervisor reads this file directly if a pre-load failure occurs, and sends the alert with `curl` (which ships with Windows 10 and 11).

#### 🪟 Windows
Create `%USERPROFILE%\.vc_notify` (e.g. `C:\Users\YourName\.vc_notify`):
```
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
CHAT_ID=987654321
```
Then harden it the same way as `.vc_secret`:
```cmd
cipher /e "%USERPROFILE%\.vc_notify"
attrib +h +s "%USERPROFILE%\.vc_notify"
icacls "%USERPROFILE%\.vc_notify" /inheritance:r /grant "%USERNAME%:R" /grant "SYSTEM:F"
```

#### 🐧 Linux
Create `/root/.vc_notify`:
```bash
sudo nano /root/.vc_notify
```
Contents:
```
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
CHAT_ID=987654321
```
Then restrict it:
```bash
sudo chmod 600 /root/.vc_notify
```

> ℹ️ This file is intentionally minimal: two lines, one credential, one purpose. It does not replace or duplicate your main `.env`. If the file does not exist the supervisor continues silently, so this step is optional but strongly recommended for unattended scheduled runs.

---

### Step 4: Initialize the Container

This is a one-time step. You must create the required folder skeleton inside the container before the first automated run.

#### 🪟 Windows
1. Open the VeraCrypt GUI, select `vaults.hc`, and mount it to drive **`Z:`**.
2. Open the `Z:` drive and create these folders:
   ```
   Z:\vaults\
   Z:\2fa\
   Z:\backups\
   ```
3. Copy your configured `.env` file **onto the `Z:` drive** (see the Configuration section below). The supervisor reads it directly from the container at runtime and loads the credentials into memory, so they are never written to your project directory.
4. Unmount from the VeraCrypt GUI.

#### 🐧 Linux
```bash
# 1. Mount manually
sudo mkdir -p /mnt/secure_vaults
sudo veracrypt --text --pim=0 --keyfiles="" --protect-hidden=no ./vaults.hc /mnt/secure_vaults

# 2. Create the skeleton
sudo mkdir -p /mnt/secure_vaults/vaults \
              /mnt/secure_vaults/2fa \
              /mnt/secure_vaults/backups

# 3. Move your configured .env inside (sourced into memory at runtime)
sudo mv .env /mnt/secure_vaults/.env

# 4. Fix ownership and unmount
sudo chown -R $(id -u):$(id -g) /mnt/secure_vaults
sudo veracrypt --text --dismount ./vaults.hc
```

---

### Step 5: Check the Supervisor Settings

Open your platform's supervisor script. The settings are at the top of the file.

| Setting | Windows (`run.bat`) | Linux (`run.sh`) |
| :--- | :--- | :--- |
| Container file | `VC_CONTAINER` (default `vaults.hc` in the project folder) | `VC_CONTAINER` (same) |
| VeraCrypt location | `VERACRYPT_EXE` | `veracrypt` from `PATH` |
| Where it mounts | `MOUNT_DRIVE` (default `Z`) | `MOUNT_POINT` (default `/mnt/secure_vaults`) |
| Linked folders | `SECURE_FOLDERS` | `SECURE_FOLDERS` |
| Password keyfile | `SECRET_FILE` (`%USERPROFILE%\.vc_secret`) | `SECRET_FILE` (`/root/.vc_secret`) |
| Fatal-alert keyfile | `NOTIFY_FILE` (`%USERPROFILE%\.vc_notify`) | `NOTIFY_FILE` (`/root/.vc_notify`) |
| Backup destination | `BACKUP_DEST` | `BACKUP_DEST` |

The one you must set is `BACKUP_DEST`, the folder where the updated `vaults.hc` is copied after each successful run (e.g. your Syncthing or cloud sync folder):

```bat
:: run.bat
set "BACKUP_DEST=D:\x\@Sync\My_Shit"
```
```bash
# run.sh
BACKUP_DEST="/data/assets/syncthing/My_Shit"
```

The copy is named `ctrl_s_master_YYYY-MM-DD.hc` and replaces any older `ctrl_s_master_*.hc` in that folder, so the folder always holds one copy.

> ℹ️ If `BACKUP_DEST` doesn't exist, or the copy fails, the run is reported as a failure. The B2 upload still goes ahead.

---

## ⚙️ Sync Job Configuration

File syncing is data-driven. **No code changes are required to add new sync jobs.**

#### 🪟 Windows (FreeFileSync)
Drop any `.ffs_batch` file into `src/_tools/ffs_jobs/`. The engine discovers and runs it automatically, as a task named `ffs-<filename>`. Ensure `FFS_PATH` is correctly set in your `.env` file so the engine knows where the FreeFileSync executable is located.

*Note: Use %AUTOMATION_ROOT% in your FreeFileSync batch file paths to refer dynamically to the project directory.*

#### 🐧 Linux (rsync)
Drop a `.json` file into `src/_tools/rsync_jobs/`. The engine parses the file and runs `rsync` as a task named `rsync-<filename>`.

Example `sync_backups.json`:
```json
{
    "source": "/home/user/backups",
    "dest": "${AUTOMATION_ROOT}/backups",
    "excludes":["_pvt", ".stfolder", ".stversions", ".git"]
}
```

*Note: Use ${AUTOMATION_ROOT} in your JSON configuration paths to refer dynamically to the project directory.*

> ⚠️ **rsync jobs mirror the source.** They run `rsync -rltDv --delete`, so a file you delete at the source is also removed from the destination on the next run. Keep that in mind for folders where the container is meant to hold history.

---

## 🔐 Configuration (`.env`)

The `.env` file lives inside the VeraCrypt container and is the single source of truth for all credentials and paths. Use `.env.example` as your starting template. It is the complete list of variables, and the tables below explain what each one does.

> ⚠️ **Security Warning:** This file handles extremely sensitive information. Keeping it inside the container ensures it is always encrypted at rest and only exposed in memory during an active run.

### Obtaining Secrets

#### A. Bitwarden Credentials

The export process uses Bitwarden Secrets Manager to retrieve credentials at runtime, so your passwords never sit in a config file.

1. **Create a Machine Account & Access Token**
   - Navigate to **Bitwarden Secrets Manager** → **Machine accounts**.
   - Create a new machine account (e.g., `vault-exporter`).
   - Under **Access tokens**, click **+ Create access token**.
   - **Copy the token immediately**, because you won't see it again. This is `BW_ACCESS_TOKEN`.

2. **Get Your Vault's API Key**
   - Go to **Bitwarden Web Vault** → **Settings** → **Security** → **Keys**.
   - Click **View API Key** to get your `client_id` and `client_secret`.
   - Repeat for each vault (personal, work) you want to export.

3. **Create Secrets and Copy UUIDs**
   - In Secrets Manager, create a **Project** and add three secrets per vault: `client_id`, `client_secret`, and `Master Password`.
   - In the project's **People** tab, grant your user account **Read** access. This is a required step.
   - Link the machine account to each secret via the **Machine accounts** tab.
   - Copy each secret's **UUID**. These fill `BW_*_CLIENT_ID_UUID`, `BW_*_CLIENT_SECRET_UUID`, and `BW_*_MASTER_PASSWORD_UUID`.

4. **Organization ID** *(only if `BW_*_IS_ORGANIZATION=true`)*
   - In the Web Vault **Admin Console**, select your Organization.
   - The ID is in the browser URL between `/organizations/` and `/vault`.

In every variable name, `*` stands for `PERSONAL` or `WORK`. A vault whose `BW_*_CLIENT_ID_UUID` is empty is skipped.

| Variable | Purpose |
| :--- | :--- |
| `BW_ACCESS_TOKEN` | The machine account's access token (step 1). |
| `BW_API_URL`, `BW_IDENTITY_URL` | Your Bitwarden cloud endpoints. `.env.example` uses `https://vault.bitwarden.com/api` and `https://vault.bitwarden.com/identity` (EU accounts use `vault.bitwarden.eu`). |
| `BW_STATE_FILE` | Where the Bitwarden SDK keeps its login state, relative to the project folder. `.env.example` points it into `vaults/`, so it stays inside the container. |
| `BW_CLI_PATH` | `bw` to use the one on your `PATH`, or a path to a specific binary. |
| `BW_*_OUTPUT_FILENAME` | Base name of the export, e.g. `bitwarden_personal.json`. Required. Files are written to `vaults/` as `<number>-<name>_<dd-mm-yyyy>.json`, with the number counting up. |
| `BW_*_EXPORT_FORMAT` | `encrypted_json` (what `.env.example` sets), `json`, `csv` or `zip`. If the variable is missing, the engine falls back to plain `json`. **Use `encrypted_json`** for a password-protected export, and stay with the JSON formats, since the numbering and rotation assume `.json` file names. |
| `BW_SERIALS_TO_KEEP` | How many exports to keep per vault. `0` keeps them all, and it's the default if unset (`.env.example` uses `5`). |
| `BW_VAULTS_DIR` | Output folder, relative to the project. Defaults to `vaults`. |

#### B. Raindrop.io API Tokens

Repeat for each account (personal, work) you want to back up.

1. Log in at [raindrop.io](https://raindrop.io) → **Settings** → **Integrations**.
2. Under **For Developers**, click **+ Create new app** and give it a name.
3. Click **Create test token** and copy it.
   - Personal token → `RAINDROP_PERSONAL_API_TOKEN`
   - Work token → `RAINDROP_WORK_API_TOKEN`

| Variable | Purpose |
| :--- | :--- |
| `RAINDROP_BACKUP_DESTINATION` | Required. The folder the backups go to, normally inside the linked container (relative paths start at the project folder). |
| `BACKUPS_TO_KEEP` | How many ZIP archives to keep per account. `0` keeps them all. Defaults to `7` if unset (`.env.example` uses `2`). |

Each run writes a ZIP (JSON, CSV and HTML versions of your bookmarks) into `<RAINDROP_BACKUP_DESTINATION>/personal/` or `/work/`, and prints an integrity check comparing the bookmark count from the API with what was downloaded.

#### C. Notification Credentials

Configure one or both channels. If both are set in `.env`, both will fire.

##### 🪟 Email (default on Windows, optional on Linux)
Use a Gmail App Password to avoid storing your real account password. Port `465` (implicit SSL) is the supported setup.

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

The report shows the result of the run, the host, the duration of each task, the offsite backup status, and the first part of any error output. It also attaches the status dashboard, plus the newest log matching each pattern in `EMAIL_ATTACH_LOGS` (a comma-separated list such as `run_*.log`).

#### D. Other Passwords & Paths

| Variable | Purpose |
| :--- | :--- |
| `BITWARDEN_*_PASSWORD` | Password that protects the exported JSON (used with `encrypted_json`). Choose anything. This is not your Bitwarden master password. The engine requires it for every configured vault. |
| `FFS_PATH` | **Windows only.** Path to `FreeFileSync.exe`. Can be relative to the project root or absolute. |
| `CONTINUE_ON_ERROR` | `true` to keep running the remaining tasks after one fails and report every failure. Defaults to `false` (stop at the first one) if unset; `.env.example` sets `true`. |
| `EMAIL_ATTACH_LOGS` | Comma-separated patterns of files in `_logs/` to attach to the report (the newest match for each pattern). |
| `STATUS_FILE`, `STATUS_DASHBOARD_FILE` | Keep the defaults. They must stay outside the container, because the report is built after it has been closed. |
| `RAINDROP_BACKUP_SCRIPT_PATH`, `BW_EXPORT_SCRIPT_PATH` | Paths to the two helper scripts. Keep the defaults. |

---

#### E. Offsite Backup (Backblaze B2)

Offsite replication to Backblaze B2 is optional and automatic. If configured, the supervisor uploads the updated container to B2 and prunes old archives using in-memory credentials. The upload is named `ctrl_s_master_YYYY-MM-DD.hc`.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `B2_APPLICATION_KEY_ID` | Your Backblaze Application Key ID (`keyID`). | *(Required for B2)* |
| `B2_APPLICATION_KEY` | Your Backblaze Application Key secret (`applicationKey`). | *(Required for B2)* |
| `B2_BUCKET` | The name of your private B2 bucket (e.g. `my-vault-backups`). | *(Required for B2)* |
| `B2_KEEP_DAYS` | Retention window. Prunes archives older than this (e.g. `30d`, `90d`). Leave it blank to disable pruning. | `30d` (`.env.example` uses `90d`) |
| `B2_MIN_COPIES` | Safety floor. Never prunes below this number of recent copies. | `3` |

* Pruning goes by the date in the file name, never by file time, and only touches files named `ctrl_s_master_*.hc`.
* If none of the B2 variables are set, the offsite backup is skipped and local backups are unaffected. If only some are set, it's reported as a failure.
* A failed offsite upload does not fail the run. It shows up on its own line in the report, and in the status dashboard.

---

## 🔒 Security & Cleanup

Secrets stay out of the project folder. The supervisor reads `.env` from the container and loads it into its own process environment, and every child process (the Python tasks, the report) inherits it from there.

* **Linux loads straight into memory.** The values go from the container into the shell with no file in between.
* **Windows uses a short-lived file inside the container. The parsed values pass through a file on the mounted container (never on the host disk), which is deleted as soon as they are loaded.
* **The container closes first.** The folder links are removed and the container is dismounted *before* the report is sent. The notification credentials are already in memory, so sending it needs no disk access.
* **Links are short-lived.** The junctions (Windows) and symlinks (Linux) into the container are removed on exit, including after a failure, so nothing is left pointing at the encrypted data.
* **Offsite credentials stay in memory too.** Backblaze B2 is configured through `RCLONE_CONFIG_B2_*` environment variables, so no `rclone.conf` or plain-text key is ever written to disk.
* **Failures before the container opens still notify me.** A wrong password or a missing container happens before `.env` is available, so the normal report can't fire. The small `.vc_notify` file outside the container covers that case (see Step 3b).

### Known trade-offs

These are deliberate choices, listed so you can weigh them:

* `.vc_secret` is a plain-text keyfile. It is hardened with EFS and file permissions on Windows, and is root-only (`chmod 600`) on Linux.
* The VeraCrypt password is passed to VeraCrypt on its command line while mounting, and the Bitwarden export password is passed to `bw export` the same way. A local user who can list processes could see them for that moment.

---

## 🕹️ Usage

### Running the Automation

#### 🪟 Windows
```cmd
run.bat
```
For scheduling, use **Windows Task Scheduler** to run `run.bat` on a regular basis (e.g., weekly), under the same user account that created `.vc_secret`.

#### 🐧 Linux
```bash
sudo ./run.sh
```

For scheduling, add to the root crontab (`sudo crontab -e`). Example for bi-weekly runs on the 2nd and 4th Friday:
```bash
0 4 8-14,22-28 * * [ "$(date +\%u)" = 5 ] && /path/to/ctrl_s_master/run.sh
```

*Note: The status dashboard and notification reports are "Config-Aware". If a vault (Work or Personal) is not configured in your .env, the task is skipped and hidden from the report to keep your dashboard clean.*

Logs for every run are written to `_logs/`, and the latest results are kept in `status.json` and `status_dashboard.md`.

---

### Tasks

The engine's tasks can be run by name, or all at once with `run-all`:

| Task | What it does |
| :--- | :--- |
| `raindrop-personal`, `raindrop-work` | Back up bookmarks. |
| `export-personal`, `export-work` | Export Bitwarden vaults. |
| `ffs-<name>` (Windows) | A FreeFileSync job from `ffs_jobs/`. |
| `rsync-<name>` (Linux) | An rsync job from `rsync_jobs/`. |

The supervisors run `run-all`. The engine only gets its credentials from the supervisor, so to run one task by hand, the variables from `.env` must already be in your environment.

---

### Dry Run

A dry run rehearses the tasks without touching the real data or sending anything:

* Bitwarden exports are written to a throwaway folder, which is deleted afterwards.
* rsync jobs run with `--dry-run`, and FreeFileSync jobs are only listed.
* No container backup, no B2 upload and no report happens.
* It still contacts Bitwarden's API.

#### 🐧 Linux
```bash
sudo ./run.sh dry
```
This still mounts the real container, because the secrets are in it.

#### 🪟 Windows
`run.bat` has no dry mode. You can run the engine directly, but it only sees credentials that are already in your environment. Without them every task reports as not configured and is skipped:
```cmd
python src\master_automation.py run-tasks run-all --dry-run
```

---

### Updating Dependencies

To update your environment, download the latest CLI tools, or repair a broken setup, run the setup script with the force flag:

#### 🪟 Windows
In a **Command Prompt as Administrator**:
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
│   ├── 🐍 master_automation.py           # The Engine: runs all tasks, backups and reports.
│   └── 📁 _tools/
│       ├── 📁 ffs_jobs/                  # Drop Windows FreeFileSync batch jobs here.
│       ├── 📁 rsync_jobs/                # Drop Linux rsync JSON config files here.
│       ├── bitwarden_exporter.py         # Exports Bitwarden vaults via API.
│       ├── raindrop_backup.py            # Downloads and archives Raindrop bookmarks.
│       └── common_utils.py               # Shared helpers (backup rotation, etc.).
│
├── 📁 _tests/                            # Pytest suite.
├── 📁 _logs/                             # Timestamped execution logs.
├── 📁 venv/                              # Isolated Python environment.
├── 📁 breakglass/                        # Emergency standalone scripts (run without the project).
│
├── 📊 status.json                        # (Gitignored) Machine-readable run history.
├── 📄 status_dashboard.md                # (Gitignored) Human-readable run dashboard.
│
│   Created at runtime, removed after:
├── 🔗 vaults/                            # Junction (Win) / Symlink (Lin) into container.
├── 🔗 2fa/                               # Junction (Win) / Symlink (Lin) into container.
├── 🔗 backups/                           # Junction (Win) / Symlink (Lin) into container.
│
│   External:
├── 🔑 ~/.vc_secret                       # Auto-unlocker keyfile (user home on Windows, /root on Linux).
└── 🔑 ~/.vc_notify                       # Fatal pre-load notification credentials (Telegram only).
```

---

### Author
This project was created and is maintained by me, [gravi-ctrl](https://github.com/gravi-ctrl).
