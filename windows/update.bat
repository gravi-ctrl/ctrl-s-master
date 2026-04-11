@echo off
setlocal

:: =================================================================
::               PROJECT DEPENDENCY UPDATER
:: =================================================================

:: --- 1. Logging Configuration ---
set "SCRIPT_DIR=%~dp0"
set "LOG_DIR=%SCRIPT_DIR%_logs\"
IF NOT EXIST "%LOG_DIR%" mkdir "%LOG_DIR%"

FOR /F "usebackq" %%I IN (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) DO set "timestamp=%%I"
set "LOG_FILE=%LOG_DIR%update_%timestamp%.log"

echo Starting dependency update...
echo Detailed log will be available at:
echo %LOG_FILE%
echo.

(
    echo =============================================================
    echo =              PROJECT DEPENDENCY UPDATER                 =
    echo =                  RUN AT: %date% %time%                  =
    echo =============================================================
    echo.
) >> "%LOG_FILE%" 2>&1

:: --- 2. Detect Python ---
set "PYTHON_CMD=python"
echo [INFO] Checking for Python interpreter... >> "%LOG_FILE%" 2>&1
"%PYTHON_CMD%" --version >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] No usable Python interpreter found. >> "%LOG_FILE%" 2>&1
    goto:end
)

:: --- 3. Rebuild Virtual Environment (Simple approach) ---
echo [STEP 1/3] Rebuilding virtual environment... >> "%LOG_FILE%" 2>&1

set "VENV_PATH=%SCRIPT_DIR%venv"
set "VENV_PYTHON=%VENV_PATH%\Scripts\python.exe"

if exist "%VENV_PATH%" (
    echo [INFO] Removing existing environment... >> "%LOG_FILE%" 2>&1
    rmdir /s /q "%VENV_PATH%"
    timeout /t 1 /nobreak >nul
)

echo [INFO] Creating fresh virtual environment... >> "%LOG_FILE%" 2>&1
%PYTHON_CMD% -m venv "%VENV_PATH%"

if not exist "%VENV_PYTHON%" (
    echo [FATAL] Failed to create venv. >> "%LOG_FILE%" 2>&1
    goto:end
)

echo [INFO] Virtual environment created successfully. >> "%LOG_FILE%" 2>&1

:: --- 4. Update Packages ---
echo [STEP 2/3] Updating Python packages... >> "%LOG_FILE%" 2>&1
set "PIP_CMD=%VENV_PYTHON% -m pip"

echo [INFO] Upgrading pip... >> "%LOG_FILE%" 2>&1
%PIP_CMD% install --upgrade pip >> "%LOG_FILE%" 2>&1

echo [INFO] Upgrading packages from requirements.txt... >> "%LOG_FILE%" 2>&1
%PIP_CMD% install -r "%SCRIPT_DIR%requirements.txt" --upgrade >> "%LOG_FILE%" 2>&1

if %errorlevel% neq 0 (
    echo [ERROR] Failed to update Python packages. >> "%LOG_FILE%" 2>&1
) else (
    echo [OK] Python packages are up-to-date. >> "%LOG_FILE%" 2>&1
)

:: --- 5. Update Bitwarden CLI ---
set "BW_CLI_DIR=%SCRIPT_DIR%src\_tools\bw\"
set "BW_DOWNLOAD_URL=https://vault.bitwarden.com/download/?app=cli&platform=windows"
set "BW_ZIP_PATH=%BW_CLI_DIR%bw.zip"

echo [STEP 3/3] Downloading latest Bitwarden CLI... >> "%LOG_FILE%" 2>&1
IF NOT EXIST "%BW_CLI_DIR%" mkdir "%BW_CLI_DIR%"

powershell -NoProfile -Command "Invoke-WebRequest -Uri '%BW_DOWNLOAD_URL%' -OutFile '%BW_ZIP_PATH%'" >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Failed to download Bitwarden CLI. >> "%LOG_FILE%" 2>&1
    goto:end
)

echo [INFO] Extracting Bitwarden CLI... >> "%LOG_FILE%" 2>&1
powershell -NoProfile -Command "Expand-Archive -Path '%BW_ZIP_PATH%' -DestinationPath '%BW_CLI_DIR%' -Force" >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Failed to extract Bitwarden CLI. >> "%LOG_FILE%" 2>&1
    goto:end
)

del "%BW_ZIP_PATH%"
echo [INFO] Bitwarden CLI is now up-to-date. >> "%LOG_FILE%" 2>&1

(
    echo =============================================================
    echo =                     UPDATE COMPLETE                       =
    echo =============================================================
) >> "%LOG_FILE%" 2>&1

:end
echo.
echo Update process finished. Check log for details.
pause
endlocal