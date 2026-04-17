@echo off
set PYTHONUTF8=1
setlocal enabledelayedexpansion

:: =================================================================
::               MASTER AUTOMATION SCRIPT (SUPERVISOR)
:: =================================================================
::  Author:         gravi-ctrl
::  Description:    Orchestrates VeraCrypt mounting, folder linking,
::                  Python execution, and cleanup.
:: =================================================================

:: --- 1. CONFIGURATION ---
set "SCRIPT_DIR=%~dp0"
set "VC_CONTAINER=%SCRIPT_DIR%vaults.hc"
set "VERACRYPT_EXE=C:\Program Files\VeraCrypt\VeraCrypt.exe"

:: List of folders to link from the Vault (Space-separated)
set "SECURE_FOLDERS=vaults 2fa backups"

set "MOUNT_DRIVE=Z"
set "SECRET_FILE=%USERPROFILE%\.vc_secret"
set "BACKUP_DEST=D:\x\@Sync\My_Shit"

:: File formatting
FOR /F "usebackq" %%I IN (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd'"`) DO set "TODAY=%%I"
set "BACKUP_FILENAME=ctrl_s_master_%TODAY%.hc"

set "LOG_DIR=%SCRIPT_DIR%_logs\"
IF NOT EXIST "%LOG_DIR%" mkdir "%LOG_DIR%"

FOR /F "usebackq" %%I IN (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) DO set "timestamp=%%I"
set "LOG_FILE=%LOG_DIR%run_%timestamp%.log"

set "PYTHON_EXE=%SCRIPT_DIR%venv\Scripts\python.exe"
set "MASTER_SCRIPT=%SCRIPT_DIR%src\master_automation.py"

:: Clean up any old failure logs before starting
del /q "%LOG_DIR%failure_details.log" 2>nul

echo Starting automation tasks...
echo Detailed log at: %LOG_FILE%
echo --- Starting Run at %date% %time% --- >> "%LOG_FILE%"

:: --- 2. MOUNT VERACRYPT ---
"%VERACRYPT_EXE%" /d %MOUNT_DRIVE% /q /s >nul 2>&1
timeout /t 2 >nul

echo Mounting container... >> "%LOG_FILE%"
if not exist "%SECRET_FILE%" (
    echo FATAL: Secret file not found at %SECRET_FILE%. >> "%LOG_FILE%"
    exit /b 1
)
set /p VC_SECRET=<"%SECRET_FILE%"

"%VERACRYPT_EXE%" /v "%VC_CONTAINER%" /l %MOUNT_DRIVE% /p "%VC_SECRET%" /pim 0 /q /s >> "%LOG_FILE%" 2>&1

if %errorlevel% neq 0 (
    echo FATAL: Failed to mount VeraCrypt container. >> "%LOG_FILE%"
    exit /b 1
)

:: --- 3. LINK FOLDERS ---
echo Linking folders... >> "%LOG_FILE%"
for %%F in (%SECURE_FOLDERS%) do (
    rmdir /s /q "%SCRIPT_DIR%%%F" 2>nul
    mklink /J "%SCRIPT_DIR%%%F" "%MOUNT_DRIVE%:\%%F" >> "%LOG_FILE%" 2>&1
)
:: Copying the .env file instead of linking to avoid strict file-symlinking restrictions
del /q "%SCRIPT_DIR%.env" 2>nul
copy /y "%MOUNT_DRIVE%:\.env" "%SCRIPT_DIR%.env" >> "%LOG_FILE%" 2>&1

:: --- 4. RUN PYTHON TASKS ---
echo Running Python Engine... >> "%LOG_FILE%"
(
    "%PYTHON_EXE%" "%MASTER_SCRIPT%" run-tasks run-all
) >> "%LOG_FILE%" 2>&1
set "PYTHON_EXIT_CODE=!errorlevel!"

:: --- PRESERVE SECRETS FOR EMAIL ---
if exist "%SCRIPT_DIR%.env" (
    copy /y "%SCRIPT_DIR%.env" "%SCRIPT_DIR%.temp_env_handoff" >nul 2>&1
    icacls "%SCRIPT_DIR%.temp_env_handoff" /inheritance:r /grant "%USERNAME%:F" >nul 2>&1
)

:: --- 5. UNMOUNT ---
echo Unmounting container... >> "%LOG_FILE%"
for %%F in (%SECURE_FOLDERS%) do (
    rmdir /q "%SCRIPT_DIR%%%F" 2>nul
)
del /q "%SCRIPT_DIR%.env" 2>nul

"%VERACRYPT_EXE%" /d %MOUNT_DRIVE% /q /s >> "%LOG_FILE%" 2>&1

:: --- 6. BACKUP CONTAINER ---
set "FINAL_EXIT_CODE=!PYTHON_EXIT_CODE!"
if !PYTHON_EXIT_CODE! equ 0 (
    echo Starting Container Backup... >> "%LOG_FILE%"
    if exist "%BACKUP_DEST%\" (
        copy /y "%VC_CONTAINER%" "%BACKUP_DEST%\%BACKUP_FILENAME%" >> "%LOG_FILE%" 2>&1
        if !errorlevel! equ 0 (
            echo ✅ Container Backup Successful. >> "%LOG_FILE%"
            for %%F in ("%BACKUP_DEST%\ctrl_s_master_*.hc") do (
                if /I not "%%~nxF"=="%BACKUP_FILENAME%" del /q "%%F" >nul 2>&1
            )
        ) else (
            echo ❌ ERROR: Failed to copy container. >> "%LOG_FILE%"
            set "FINAL_EXIT_CODE=1"
        )
    ) else (
        echo ❌ ERROR: Backup destination not found. >> "%LOG_FILE%"
        set "FINAL_EXIT_CODE=1"
    )
)

:: --- 7. SEND REPORT ---
if exist "%SCRIPT_DIR%.temp_env_handoff" (
    copy /y "%SCRIPT_DIR%.temp_env_handoff" "%SCRIPT_DIR%.env" >nul 2>&1
)

if !FINAL_EXIT_CODE! equ 0 (
    "%PYTHON_EXE%" "%MASTER_SCRIPT%" send-report success > NUL 2>&1
) else (
    "%PYTHON_EXE%" "%MASTER_SCRIPT%" send-report failure > NUL 2>&1
)

:: --- 8. FINAL CLEANUP ---
del /q "%SCRIPT_DIR%.env" 2>nul
del /q "%SCRIPT_DIR%.temp_env_handoff" 2>nul

echo Automation finished. Final Code: !FINAL_EXIT_CODE!
echo --- Finished at %date% %time% --- >> "%LOG_FILE%"
exit /b !FINAL_EXIT_CODE!