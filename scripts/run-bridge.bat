@echo off
REM Radio↔Discord bridge launcher for Windows Task Scheduler.
REM Logs to logs\bridge.log (rotation not handled here — truncate manually
REM if it grows too large, or wrap in PowerShell with Add-Content rotation).
REM
REM Auto-restart loop: if python exits (crash, network drop, ctrl-c isn't
REM possible inside a Task Scheduler session), wait 5 s then restart.

setlocal
cd /d "%~dp0.."

set "LOGDIR=%cd%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOGFILE=%LOGDIR%\bridge.log"

:loop
echo. >> "%LOGFILE%"
echo ===== %date% %time% starting bridge ===== >> "%LOGFILE%"
python -m radio_discord_bridge >> "%LOGFILE%" 2>&1
echo ===== %date% %time% bridge exited (code %errorlevel%) ===== >> "%LOGFILE%"
timeout /t 5 /nobreak > nul
goto loop
