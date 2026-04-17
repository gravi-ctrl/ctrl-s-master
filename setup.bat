@echo off
setlocal

:: =================================================================
::               PROJECT INITIAL SETUP
:: =================================================================
::
::  Purpose: To set up the entire project from scratch on a
::           new machine. Run this only once.
::
:: =================================================================

:: --- Phase 1: Logging Configuration ---
set "SCRIPT_DIR=%~dp0"
set "LOG_DIR=%SCRIPT_DIR%_logs\"
IF NOT EXIST "%LOG_DIR%" mkdir "%LOG_DIR%"

FOR /F "usebackq" %%I IN (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) DO set "timestamp=%%I"
set "LOG_FILE=%LOG_DIR%setup_%timestamp%.log"

echo Starting environment setup...
echo Detailed log will be available at:
echo %LOG_FILE%
echo.

(
    echo =============================================================
    echo =          AUTOMATED PYTHON ENVIRONMENT SETUP             =
    echo =                  RUN AT: %date% %time%                  =
    echo =============================================================
    echo.
) >> "%LOG_FILE%" 2>&1

set "VENV_DIR=.\venv"
set "REQUIREMENTS_FILE=requirements.txt"
set "BW_CLI_DIR=%SCRIPT_DIR%src\_tools\bw\"
set "BW_DOWNLOAD_URL=https://vault.bitwarden.com/download/?app=cli&platform=windows"
set "BW_ZIP_PATH=%BW_CLI_DIR%bw.zip"

:: --- Step 1: Check for Python ---
set "PYTHON_CMD=python"
echo [STEP 1/4] Checking for usable Python interpreter... >> "%LOG_FILE%" 2>&1
"%PYTHON_CMD%" --version >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] No usable Python interpreter found. Exiting. >> "%LOG_FILE%" 2>&1
    exit /b 1
)
echo Python interpreter found. >> "%LOG_FILE%" 2>&1
echo. >> "%LOG_FILE%" 2>&1

:: --- Step 2: Create or Verify Virtual Environment ---
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
echo [STEP 2/4] Setting up Python virtual environment in '%VENV_DIR%'... >> "%LOG_FILE%" 2>&1
if not exist "%VENV_PYTHON%" (
    echo [INFO] Creating new virtual environment... >> "%LOG_FILE%" 2>&1
    ("%PYTHON_CMD%" -m venv %VENV_DIR%) >> "%LOG_FILE%" 2>&1
    if %errorlevel% neq 0 (
        echo [FATAL] Failed to create venv. Exiting. >> "%LOG_FILE%" 2>&1
        exit /b 1
    )
    echo [INFO] Virtual environment created successfully. >> "%LOG_FILE%" 2>&1
) else (
    echo [INFO] Existing venv found. Skipping creation. >> "%LOG_FILE%" 2>&1
)
echo. >> "%LOG_FILE%" 2>&1

:: --- Step 3: Activate and Install/Upgrade Python Packages ---
echo [STEP 3/4] Activating environment and installing packages... >> "%LOG_FILE%" 2>&1
set "PIP_CMD=%VENV_PYTHON% -m pip"
echo [INFO] Upgrading pip to latest... >> "%LOG_FILE%" 2>&1
(%PIP_CMD% install --upgrade pip) >> "%LOG_FILE%" 2>&1
echo Installing packages from %REQUIREMENTS_FILE%... >> "%LOG_FILE%" 2>&1
(%PIP_CMD% install -r %REQUIREMENTS_FILE%) >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Failed to install Python packages. Exiting. >> "%LOG_FILE%" 2>&1
    exit /b 1
)
echo Python packages installed. >> "%LOG_FILE%" 2>&1
echo. >> "%LOG_FILE%" 2>&1

:: --- Step 4: Install/Update Bitwarden CLI ---
echo [STEP 4/4] Downloading Bitwarden CLI... >> "%LOG_FILE%" 2>&1
IF NOT EXIST "%BW_CLI_DIR%" mkdir "%BW_CLI_DIR%"
(powershell -NoProfile -Command "Invoke-WebRequest -Uri '%BW_DOWNLOAD_URL%' -OutFile '%BW_ZIP_PATH%'") >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Failed to download Bitwarden CLI. Exiting. >> "%LOG_FILE%" 2>&1
    exit /b 1
)

echo Extracting Bitwarden CLI... >> "%LOG_FILE%" 2>&1
(powershell -NoProfile -Command "Expand-Archive -Path '%BW_ZIP_PATH%' -DestinationPath '%BW_CLI_DIR%' -Force") >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Failed to extract Bitwarden CLI. Exiting. >> "%LOG_FILE%" 2>&1
    exit /b 1
)
del "%BW_ZIP_PATH%"
echo [INFO] Bitwarden CLI is now up-to-date in '%BW_CLI_DIR%'. >> "%LOG_FILE%" 2>&1
echo. >> "%LOG_FILE%" 2>&1

(
    echo =============================================================
    echo =                      SETUP COMPLETE                       =
    echo =============================================================
) >> "%LOG_FILE%" 2>&1

echo.
echo Setup finished. Check log for details.
pause
endlocal