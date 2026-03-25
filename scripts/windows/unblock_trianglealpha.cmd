@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "TARGET_PATH=%~1"

if "%TARGET_PATH%"=="" set "TARGET_PATH=%CD%"

powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%unblock_trianglealpha.ps1" "%TARGET_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo 解除锁定失败，退出码: %EXIT_CODE%
    pause
)

exit /b %EXIT_CODE%
