@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title ZT Color Match Studio
if not exist "%~dp0ZTColorStudio.exe" (
  echo Windows launcher is missing. Extract the complete Windows Release ZIP.
  pause
  exit /b 1
)
"%~dp0ZTColorStudio.exe" %*
set "ZT_EXIT=%ERRORLEVEL%"
if not "%ZT_EXIT%"=="0" if "%~1"=="" pause
exit /b %ZT_EXIT%
