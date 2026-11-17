@echo off
if exist "%~dp0aemeath.log" (
  start "" notepad "%~dp0aemeath.log"
) else (
  echo No log file yet. Run install_task.bat first.
  pause
)
