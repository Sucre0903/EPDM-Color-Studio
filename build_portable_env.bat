@echo off
setlocal enabledelayedexpansion

REM Store our own full path in a variable BEFORE any parenthesized block.
REM See the comment in start.bat: expanding %~f0 directly inside a ( ... )
REM block breaks the block's paren matching if the path contains literal
REM parentheses. Using !SELF! (delayed expansion) avoids that.
set "SELF=%~f0"

REM Safety net: relaunch this script inside a "cmd /k" window so the
REM window stays open no matter what happens below (crash, error, etc).
if not "%~1"=="RUNNING" (
    start "" cmd /k ""!SELF!" RUNNING"
    exit /b
)

cd /d "%~dp0"

echo ==================================================================
echo   FK Color Studio - Build Offline Portable Environment
echo.
echo   Run this ONCE on any PC with internet access. When it finishes,
echo   a "python_embed" folder will appear next to app.py. Zip up the
echo   whole project folder (including python_embed) and send it to
echo   colleagues - they just double-click start.bat, no install needed.
echo ==================================================================
echo.
echo This script must be placed in the project folder, next to app.py
echo and requirements.txt.
echo.
pause

set "PYVER=3.11.9"
set "EMBED_ZIP=python-%PYVER%-embed-amd64.zip"
set "EMBED_URL_MIRROR=https://mirrors.huaweicloud.com/python/%PYVER%/%EMBED_ZIP%"
set "EMBED_URL_OFFICIAL=https://www.python.org/ftp/python/%PYVER%/%EMBED_ZIP%"
set "GETPIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "TARGET_DIR=python_embed"

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found in this folder.
    echo Make sure this script sits next to app.py and requirements.txt.
    pause
    exit /b 1
)

if exist "%TARGET_DIR%" (
    echo Found an existing %TARGET_DIR% folder, removing it to rebuild fresh...
    rmdir /s /q "%TARGET_DIR%"
)
mkdir "%TARGET_DIR%"

echo.
echo [1/6] Downloading Python %PYVER% embeddable runtime (trying China mirror first)...
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%EMBED_URL_MIRROR%' -OutFile '%EMBED_ZIP%' -TimeoutSec 60 } catch { exit 1 }"
if not exist "%EMBED_ZIP%" (
    echo Mirror failed, trying the official python.org address instead...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%EMBED_URL_OFFICIAL%' -OutFile '%EMBED_ZIP%' -TimeoutSec 60 } catch { exit 1 }"
)
if not exist "%EMBED_ZIP%" goto :download_fail

echo [2/6] Extracting...
powershell -NoProfile -Command "Expand-Archive -Path '%EMBED_ZIP%' -DestinationPath '%TARGET_DIR%' -Force"
del "%EMBED_ZIP%"

echo [3/6] Enabling pip / third-party packages (disabled by default in embeddable Python)...
set "PTH_FILE="
for %%f in ("%TARGET_DIR%\python*._pth") do set "PTH_FILE=%%f"
if not defined PTH_FILE goto :pth_fail
powershell -NoProfile -Command ^
  "$c = Get-Content '%PTH_FILE%'; $c = $c -replace '#import site','import site'; $c += 'Lib\site-packages'; $c | Set-Content '%PTH_FILE%'"

echo [4/6] Downloading and installing pip...
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%GETPIP_URL%' -OutFile 'get-pip.py' -TimeoutSec 60 } catch { exit 1 }"
if not exist "get-pip.py" goto :getpip_fail
"%TARGET_DIR%\python.exe" get-pip.py -i %PIP_MIRROR% --no-warn-script-location
del get-pip.py

echo [5/6] Installing project dependencies (streamlit / pandas / pillow etc) via China mirror.
echo       This step is slow, please wait a few minutes...
"%TARGET_DIR%\python.exe" -m pip install -i %PIP_MIRROR% --no-warn-script-location -r requirements.txt
if errorlevel 1 (
    echo Mirror install failed, retrying with the default PyPI source...
    "%TARGET_DIR%\python.exe" -m pip install --no-warn-script-location -r requirements.txt
)
if errorlevel 1 goto :deps_fail

echo [6/6] Verifying installation...
"%TARGET_DIR%\python.exe" -m streamlit --version
if errorlevel 1 goto :verify_fail

echo.
echo ==================================================================
echo   Done! The python_embed folder now contains a full runtime
echo   environment (a few hundred MB).
echo.
echo   Next steps:
echo   1. Zip the ENTIRE project folder (must include python_embed)
echo   2. Share it with colleagues (use cloud drive, not email - it's big)
echo   3. Each colleague should COPY the folder to their own local disk
echo      (e.g. Desktop) before double-clicking start.bat - running it
echo      directly off a shared network drive can hit permission/locking
echo      issues, especially with several people using it at once.
echo.
echo   Note: On first run, Windows may show a "Windows protected your PC"
echo   warning because the files came from the internet. That's normal -
echo   click "More info" then "Run anyway".
echo ==================================================================
pause
exit /b 0

:download_fail
echo [ERROR] Failed to download the Python embeddable package from either
echo the mirror or the official site. Check your internet connection.
pause
exit /b 1

:pth_fail
echo [ERROR] Could not find the ._pth config file inside %TARGET_DIR%.
echo The embeddable package may not have downloaded/extracted correctly.
echo Please run this script again.
pause
exit /b 1

:getpip_fail
echo [ERROR] Failed to download get-pip.py. Check your internet connection.
pause
exit /b 1

:deps_fail
echo [ERROR] Failed to install dependencies. See the error message above.
echo This is often a temporary network issue - try running this script again.
pause
exit /b 1

:verify_fail
echo [ERROR] Verification failed, streamlit may not be installed correctly.
echo Please run this script again.
pause
exit /b 1
