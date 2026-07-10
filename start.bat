@echo off
setlocal enabledelayedexpansion

REM Store our own full path in a variable BEFORE any parenthesized block.
REM This avoids a classic batch-file bug: if the path contains literal
REM parentheses (e.g. a folder named "Software (v2)"), expanding %~f0
REM directly INSIDE a ( ... ) block can break the block's own paren
REM matching and crash the script instantly. Using a delayed-expansion
REM variable (!SELF!) instead sidesteps that entirely.
set "SELF=%~f0"

REM Safety net: relaunch this script inside a "cmd /k" window so the
REM window stays open no matter what happens below (crash, error, etc).
if not "%~1"=="RUNNING" (
    start "" cmd /k ""!SELF!" RUNNING"
    exit /b
)

cd /d "%~dp0"

echo ============================================
echo   FK Color Studio - Starting
echo ============================================
echo.

REM ---------- Prefer the bundled offline environment if present ----------
if exist "python_embed\python.exe" (
    set "PYCMD=python_embed\python.exe"
    echo Found built-in offline environment. Using it directly, no internet needed.
    goto :run
)

REM ---------- Otherwise look for a system-installed Python 3.8+ ----------
set "PYCMD="
call :try_python "py -3.12"
call :try_python "py -3.11"
call :try_python "py -3.10"
call :try_python "py -3.9"
call :try_python "py -3.8"
call :try_python "py -3"
call :try_python "python"
call :try_python "python3"

if not defined PYCMD goto :no_python

echo Found system Python:
%PYCMD% --version
echo.
echo Upgrading pip...
%PYCMD% -m pip install --upgrade pip --quiet --disable-pip-version-check >nul 2>&1
echo Installing required packages, please keep internet connected...
%PYCMD% -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if errorlevel 1 goto :install_fail

:run
echo.
echo Starting... your browser should open automatically in a few seconds.
echo Close this window to stop the program.
echo.
%PYCMD% -m streamlit run app.py
echo.
echo [Program stopped]
pause
exit /b 0

:try_python
if defined PYCMD exit /b 0
%~1 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=%~1"
exit /b 0

:no_python
echo [ERROR] No offline environment found, and no Python 3.8+ found on this PC.
echo.
echo Option A: Ask whoever built the package for a full copy that includes
echo           the "python_embed" folder (fully offline, no install needed).
echo Option B: Install Python yourself from https://www.python.org/downloads/
echo           IMPORTANT: check "Add python.exe to PATH" during installation.
echo           Then double-click this file again.
echo.
pause
exit /b 1

:install_fail
echo.
echo [ERROR] Failed to install required packages. See the error message above.
echo Common causes: no internet access to pypi.org, a company proxy/firewall
echo blocking it, or the Python version is too old.
echo Recommended fix: use the offline package that includes "python_embed".
echo.
pause
exit /b 1
