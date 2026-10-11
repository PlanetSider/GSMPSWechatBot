@echo off
title WeFlow Push (standalone)

cd /d %~dp0

set "PORT=5031"

rem Check whether port %PORT% is already in use (weflow or another program).
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [weflow] Port %PORT% is already in use, treat as weflow running, skip start.
    exit /b 0
)

if not exist "weflow-core\WeFlow.exe" (
    echo [weflow] weflow-core\WeFlow.exe not found. Run start.bat first to build.
    exit /b 1
)
if not exist "weflow-core\dist\index.js" (
    echo [weflow] weflow-core\dist\index.js not found. Run start.bat first to build.
    exit /b 1
)

echo [weflow] Starting weflow-core (push service) standalone...
cd /d "weflow-core"
WeFlow.exe dist\index.js ..\config.json