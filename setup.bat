@echo off
set PYTHONUTF8=1
setlocal enabledelayedexpansion

:: =================================================================
::               PROJECT MASTER SETUP & UPDATER
:: =================================================================
:: Usage:
::   setup.bat          - Standard setup / Quick health check
::   setup.bat /f       - Force rebuild (Nuclear update)
::
:: NOTE: Must be run as Administrator for Chocolatey to work!
:: =================================================================

set "SCRIPT_DIR=%~dp0"
set "LOG_DIR=%SCRIPT_DIR%_logs\"
IF NOT EXIST "%LOG_DIR%" mkdir "%LOG_DIR%"

FOR /F "usebackq" %%I IN (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) DO set "timestamp=%%I"
set "LOG_FILE=%LOG_DIR%setup_%timestamp%.log"

echo Initializing environment...
echo This window will remain black. Detailed log will be at:
echo %LOG_FILE%

(
    echo =============================================================
    echo =              PROJECT SETUP & UPDATE ENGINE                =
    echo =                  RUN AT: %date% %time%                    =
    echo =============================================================
) > "%LOG_FILE%" 2>&1

set "VENV_DIR=%SCRIPT_DIR%venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQUIREMENTS_FILE=%SCRIPT_DIR%requirements.txt"
set "PYTHON_CMD=python"

:: --- Step 1: Detect Python ---
"%PYTHON_CMD%" --version >> "%LOG_FILE%" 2>&1
if %errorlevel% neq 0 (
    echo [FATAL] No Python interpreter found. >> "%LOG_FILE%" 2>&1
    goto :end
)

:: --- Step 2: Handle "Nuclear" Rebuild ---
if "%1"=="/f" (
    echo [FORCE] Nuclear rebuild triggered. Wiping existing environment... >> "%LOG_FILE%" 2>&1
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%" >> "%LOG_FILE%" 2>&1
    timeout /t 1 /nobreak >nul
)

:: --- Step 3: Create or Verify VENV ---
if not exist "%VENV_PYTHON%" (
    echo [INFO] Creating new virtual environment... >> "%LOG_FILE%" 2>&1
    ("%PYTHON_CMD%" -m venv "%VENV_DIR%") >> "%LOG_FILE%" 2>&1
) else (
    echo [INFO] Environment exists. Performing health check... >> "%LOG_FILE%" 2>&1
)

:: --- Step 4: Sync Packages ---
set "PIP_CMD="%VENV_PYTHON%" -m pip"
echo [INFO] Updating pip and dependencies... >> "%LOG_FILE%" 2>&1
(%PIP_CMD% install --upgrade pip) >> "%LOG_FILE%" 2>&1
(%PIP_CMD% install -r "%REQUIREMENTS_FILE%" --upgrade) >> "%LOG_FILE%" 2>&1

:: --- Step 5: System Bitwarden CLI (Chocolatey) ---
echo [INFO] Syncing system Bitwarden CLI via Chocolatey... >> "%LOG_FILE%" 2>&1
where choco >nul 2>nul
if !errorlevel! neq 0 (
    echo [FATAL] Chocolatey is not installed or not on PATH! >> "%LOG_FILE%" 2>&1
    echo [INFO] Please install Chocolatey (chocolatey.org) to manage the Bitwarden CLI. >> "%LOG_FILE%" 2>&1
    goto :end
)

:: 'upgrade' automatically installs the package if it is missing
(choco upgrade bitwarden-cli -y) >> "%LOG_FILE%" 2>&1

(
    echo.
    echo =============================================================
    echo =                      PROCESS COMPLETE                     =
    echo =============================================================
) >> "%LOG_FILE%" 2>&1

:end
echo.
echo Process finished. Check log for details.
pause
endlocal