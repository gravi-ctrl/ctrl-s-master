@echo off
setlocal

:: =================================================================
::               AUTOMATION SUITE TEST SUITE LAUNCHER
:: =================================================================
::
::  Author:         gravi-ctrl
::  Created:        2025-05-17
::  Last Modified:  2025-11-06
::
::  Description:    A simple launcher for the automated test suite
::
::
:: =================================================================

echo Activating virtual environment and starting test suite...
echo.

:: Define the path to the venv activation script
set "VENV_ACTIVATE=%~dp0venv\Scripts\activate.bat"

:: Call the activation script and then immediately run pytest.
:: The '-v' flag provides verbose output.
call "%VENV_ACTIVATE%" && pytest -v

echo.
echo Test suite finished.
pause