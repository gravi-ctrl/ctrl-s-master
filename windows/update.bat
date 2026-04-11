@echo off
setlocal

:: =================================================================
::               PROJECT DEPENDENCY UPDATER
:: =================================================================
::
::  Purpose: Automatically fixes path/drive issues, then updates 
::           all dependencies to latest versions.
::
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

:: --- 1.5. Detect Python (same as setup.bat) ---
set "PYTHON_CMD=python"
echo [INFO] Checking for Python interpreter... >> "%LOG_FILE%" 2>&1
"%PYTHON_CMD%" --version >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] No usable Python interpreter found. >> "%LOG_FILE%" 2>&1
    goto:end
)

:: --- 2. Check & Fix Virtual Environment (Portability Fix) ---
echo [STEP 1/3] Verifying Virtual Environment Integrity... >> "%LOG_FILE%" 2>&1

set "VENV_PATH=%SCRIPT_DIR%venv"
set "ACTIVATE_SCRIPT=%VENV_PATH%\Scripts\activate.bat"
set "VENV_PYTHON=%VENV_PATH%\Scripts\python.exe"
set "NEEDS_REBUILD=0"

:: Check if venv exists and if it matches current location
if exist "%ACTIVATE_SCRIPT%" (
    findstr /I /C:"VIRTUAL_ENV=%VENV_PATH%" "%ACTIVATE_SCRIPT%" >nul
    if errorlevel 1 (
        echo [WARN] Drive letter or path mismatch detected. Venv is likely broken. >> "%LOG_FILE%" 2>&1
        set "NEEDS_REBUILD=1"
    )
) else (
    echo [INFO] Virtual environment not found. Creating new one... >> "%LOG_FILE%" 2>&1
    set "NEEDS_REBUILD=1"
)

if "%NEEDS_REBUILD%"=="1" (
    if exist "%VENV_PATH%" (
        echo [INFO] Removing broken environment... >> "%LOG_FILE%" 2>&1
        rmdir /s /q "%VENV_PATH%" >> "%LOG_FILE%" 2>&1
        timeout /t 2 /nobreak >nul
    )
    echo [INFO] Creating fresh virtual environment... >> "%LOG_FILE%" 2>&1
    ("%PYTHON_CMD%" -m venv "%VENV_PATH%") >> "%LOG_FILE%" 2>&1
    if %errorlevel% neq 0 (
        echo [FATAL] Failed to create venv. Check the log for details. >> "%LOG_FILE%" 2>&1
        goto:end
    )
    if not exist "%VENV_PYTHON%" (
        echo [FATAL] Venv created but python.exe not found. >> "%LOG_FILE%" 2>&1
        goto:end
    )
    echo [INFO] Virtual environment created successfully. >> "%LOG_FILE%" 2>&1
)

:: --- 3. Activate and Update Packages ---
echo [STEP 2/3] Activating environment and updating Python packages... >> "%LOG_FILE%" 2>&1
set "PIP_CMD=%VENV_PYTHON% -m pip"

echo [INFO] Upgrading pip to latest... >> "%LOG_FILE%" 2>&1
(%PIP_CMD% install --upgrade pip) >> "%LOG_FILE%" 2>&1

echo [INFO] Upgrading packages from requirements.txt... >> "%LOG_FILE%" 2>&1
(%PIP_CMD% install -r "%SCRIPT_DIR%requirements.txt" --upgrade) >> "%LOG_FILE%" 2>&1

if %errorlevel% neq 0 (
    echo [ERROR] Failed to update Python packages. Check the log. >> "%LOG_FILE%" 2>&1
) else (
    echo [OK] Python packages are up-to-date. >> "%LOG_FILE%" 2>&1
)
echo. >> "%LOG_FILE%" 2>&1

:: --- 4. Update Bitwarden CLI ---
set "BW_CLI_DIR=%SCRIPT_DIR%src\_tools\bw\"
set "BW_DOWNLOAD_URL=https://vault.bitwarden.com/download/?app=cli&platform=windows"
set "BW_ZIP_PATH=%BW_CLI_DIR%bw.zip"

echo [STEP 3/3] Downloading latest Bitwarden CLI... >> "%LOG_FILE%" 2>&1
IF NOT EXIST "%BW_CLI_DIR%" mkdir "%BW_CLI_DIR%"
(powershell -NoProfile -Command "Invoke-WebRequest -Uri '%BW_DOWNLOAD_URL%' -OutFile '%BW_ZIP_PATH%'") >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Failed to download Bitwarden CLI. Check log for details. >> "%LOG_FILE%" 2>&1
    goto:end
)

echo Extracting Bitwarden CLI... >> "%LOG_FILE%" 2>&1
(powershell -NoProfile -Command "Expand-Archive -Path '%BW_ZIP_PATH%' -DestinationPath '%BW_CLI_DIR%' -Force") >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] Failed to extract Bitwarden CLI. Check log for details. >> "%LOG_FILE%" 2>&1
    goto:end
)
del "%BW_ZIP_PATH%"
echo [INFO] Bitwarden CLI is now up-to-date. >> "%LOG_FILE%" 2>&1
echo. >> "%LOG_FILE%" 2>&1

(
    echo =============================================================
    echo =                     UPDATE COMPLETE                       =
    echo =============================================================
    echo IMPORTANT: Now run 'run_tests.bat' to verify these updates.
) >> "%LOG_FILE%" 2>&1

:end
echo.
echo Update process finished. Check log for details.
pause
endlocal