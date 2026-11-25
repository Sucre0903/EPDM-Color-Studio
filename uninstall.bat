@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title ZT Color Match Studio Uninstall
set "ZT_UNINSTALL_ACTION="
if /i "%~1"=="--keep-runtime" set "ZT_UNINSTALL_ACTION=keep"
if /i "%~1"=="--purge-runtime" set "ZT_UNINSTALL_ACTION=purge"
if not "%~1"=="" if not defined ZT_UNINSTALL_ACTION exit /b 2
if not defined ZT_UNINSTALL_ACTION (
  choice /c KPQ /n /m "[K] Remove this copy and keep shared runtime. [P] Remove this copy and shared runtime. [Q] Cancel. "
  if errorlevel 3 exit /b 0
  if errorlevel 2 (set "ZT_UNINSTALL_ACTION=purge") else (set "ZT_UNINSTALL_ACTION=keep")
)
"%~dp0ZTColorStudio.exe" --maintenance %ZT_UNINSTALL_ACTION%
if errorlevel 1 (
  if "%~1"=="" pause
  exit /b 1
)
for %%I in ("%~dp0.") do set "ZT_UNINSTALL_ROOT=%%~fI"
cd /d "%TEMP%" || (
  echo Could not move to the temporary folder. Delete "%ZT_UNINSTALL_ROOT%" from Explorer.
  if "%~1"=="" pause
  exit /b 1
)
(
  rmdir /s /q "%ZT_UNINSTALL_ROOT%"
  if exist "%ZT_UNINSTALL_ROOT%\" (
    echo Could not remove "%ZT_UNINSTALL_ROOT%". Close any open files and delete this folder.
    if "%~1"=="" pause
    exit /b 1
  )
  echo Application folder removed.
  if "%~1"=="" pause
  exit /b 0
)
