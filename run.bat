@echo off
set PYTHONUTF8=1
setlocal enabledelayedexpansion

:: =================================================================
::               MASTER AUTOMATION SCRIPT (SUPERVISOR)
:: =================================================================
::
::  Author:         gravi-ctrl
::  Created:        2025-05-17
::  Last Modified:  2025-11-06
::
::  Description:    The master supervisor script that orchestrates
::                  the entire automation workflow.
::
:: =================================================================

:: --- 1. CONFIGURATION & Setup Paths ---
set "SCRIPT_DIR=%~dp0"
set "VC_CONTAINER=%SCRIPT_DIR%vaults.hc"

:: --- VERACRYPT & BACKUP CONFIGURATION ---
set "MOUNT_DRIVE=Z"
set "SECRET_FILE=%USERPROFILE%\.vc_secret"
set "BACKUP_DEST=D:\x\@Sync\My_Shit"
set "VERACRYPT_EXE=C:\Program Files\VeraCrypt\VeraCrypt.exe"

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

:: Initial Console Output
echo Starting automation tasks...
echo This window will remain black. Detailed log will be at:
echo %LOG_FILE%

echo --- Starting Run at %date% %time% --- >> "%LOG_FILE%"

:: --- 2. MOUNT VERACRYPT (Clean Logic) ---
:: Pre-cleanup
"%VERACRYPT_EXE%" /d %MOUNT_DRIVE% /q /s >nul 2>&1
timeout /t 2 >nul

echo Mounting container... >> "%LOG_FILE%"
if not exist "%SECRET_FILE%" (
    echo FATAL: Secret file not found at %SECRET_FILE%. >> "%LOG_FILE%"
    exit /b 1
)
:: Read the secret from the plain text file
set /p VC_SECRET=<"%SECRET_FILE%"

:: FIXED: Removed /keyfiles "" and /protecthidden no. They cause parsing errors on Windows.
"%VERACRYPT_EXE%" /v "%VC_CONTAINER%" /l %MOUNT_DRIVE% /p "%VC_SECRET%" /pim 0 /q /s >> "%LOG_FILE%" 2>&1

if %errorlevel% neq 0 (
    echo FATAL: Failed to mount VeraCrypt container. >> "%LOG_FILE%"
    exit /b 1
)

:: --- 3. LINK FOLDERS ---
echo Linking folders... >> "%LOG_FILE%"
rmdir /s /q "%SCRIPT_DIR%vaults" 2>nul
rmdir /s /q "%SCRIPT_DIR%2fa" 2>nul
rmdir /s /q "%SCRIPT_DIR%backups" 2>nul
del /q "%SCRIPT_DIR%.env" 2>nul

:: Using Directory Junctions (/J) as they act identical to Linux symlinks but don't require Administrator UAC
mklink /J "%SCRIPT_DIR%vaults" "%MOUNT_DRIVE%:\vaults" >> "%LOG_FILE%" 2>&1
mklink /J "%SCRIPT_DIR%2fa" "%MOUNT_DRIVE%:\2fa" >> "%LOG_FILE%" 2>&1
mklink /J "%SCRIPT_DIR%backups" "%MOUNT_DRIVE%:\backups" >> "%LOG_FILE%" 2>&1
:: Copying the .env file instead of linking to avoid strict file-symlinking restrictions
copy /y "%MOUNT_DRIVE%:\.env" "%SCRIPT_DIR%.env" >> "%LOG_FILE%" 2>&1

:: --- 4. RUN PYTHON TASKS (PHASE 1) ---
echo Running Python Engine... >> "%LOG_FILE%"
(
    "%PYTHON_EXE%" "%MASTER_SCRIPT%" run-tasks run-all
) >> "%LOG_FILE%" 2>&1

:: Capture the exit code from Phase 1
set "PYTHON_EXIT_CODE=!errorlevel!"

:: --- PRESERVE SECRETS FOR EMAIL ---
:: We need the .env file to send the email, but we must unmount the container first.
if exist "%SCRIPT_DIR%.env" (
    copy /y "%SCRIPT_DIR%.env" "%SCRIPT_DIR%.temp_env_handoff" >nul 2>&1
    :: Equivalent to chmod 600 - restricts to current user only
    icacls "%SCRIPT_DIR%.temp_env_handoff" /inheritance:r /grant "%USERNAME%:F" >nul 2>&1
)

:: --- 5. UNMOUNT ---
echo Unmounting container... >> "%LOG_FILE%"
:: rmdir on a Junction only removes the pointer, leaving your master data secure!
rmdir /q "%SCRIPT_DIR%vaults" 2>nul
rmdir /q "%SCRIPT_DIR%2fa" 2>nul
rmdir /q "%SCRIPT_DIR%backups" 2>nul
del /q "%SCRIPT_DIR%.env" 2>nul

"%VERACRYPT_EXE%" /d %MOUNT_DRIVE% /q /s >> "%LOG_FILE%" 2>&1

:: --- 6. BACKUP CONTAINER (Copy & Rotate) ---
set "FINAL_EXIT_CODE=!PYTHON_EXIT_CODE!"

if !PYTHON_EXIT_CODE! equ 0 (
    echo Starting Container Backup... >> "%LOG_FILE%"
    
    if exist "%BACKUP_DEST%\" (
        echo Copying to: %BACKUP_DEST%\%BACKUP_FILENAME% >> "%LOG_FILE%"
        copy /y "%VC_CONTAINER%" "%BACKUP_DEST%\%BACKUP_FILENAME%" >> "%LOG_FILE%" 2>&1
        
        if !errorlevel! equ 0 (
            echo ✅ Container Backup Successful. >> "%LOG_FILE%"
            echo Cleaning up older container backups... >> "%LOG_FILE%"
            :: Loop through and delete all .hc files EXCEPT the one we just copied today
            for %%F in ("%BACKUP_DEST%\ctrl_s_master_*.hc") do (
                if /I not "%%~nxF"=="%BACKUP_FILENAME%" del /q "%%F" >nul 2>&1
            )
        ) else (
            echo ❌ ERROR: Failed to copy container file. Old backup preserved. >> "%LOG_FILE%"
            set "FINAL_EXIT_CODE=1"
        )
    ) else (
        echo ❌ ERROR: Backup destination not found: %BACKUP_DEST% >> "%LOG_FILE%"
        set "FINAL_EXIT_CODE=1"
    )
) else (
    echo ⚠️ Skipping Container Backup because Python tasks failed. >> "%LOG_FILE%"
)

:: --- 7. PHASE 2 & 3: SEND REPORT ---
:: Move the temp env file back so Python can read it for the email handoff
if exist "%SCRIPT_DIR%.temp_env_handoff" (
    copy /y "%SCRIPT_DIR%.temp_env_handoff" "%SCRIPT_DIR%.env" >nul 2>&1
)

echo. >> "%LOG_FILE%"
if !FINAL_EXIT_CODE! equ 0 (
    ( "%PYTHON_EXE%" "%MASTER_SCRIPT%" send-report success --generate-log-only ) >> "%LOG_FILE%" 2>&1
    "%PYTHON_EXE%" "%MASTER_SCRIPT%" send-report success > NUL 2>&1
) else (
    ( "%PYTHON_EXE%" "%MASTER_SCRIPT%" send-report failure --generate-log-only ) >> "%LOG_FILE%" 2>&1
    "%PYTHON_EXE%" "%MASTER_SCRIPT%" send-report failure > NUL 2>&1
)

:: --- 8. FINAL CLEANUP ---
del /q "%SCRIPT_DIR%.env" 2>nul
del /q "%SCRIPT_DIR%.temp_env_handoff" 2>nul

:: Final Console Message
echo.
echo Automation run finished. Check log for details:
echo %LOG_FILE%
echo --- Finished at %date% %time% --- >> "%LOG_FILE%"

:: Exit with the final error code
exit /b !FINAL_EXIT_CODE!