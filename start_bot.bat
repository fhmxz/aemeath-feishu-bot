@echo off
cd /d "%~dp0"

if not exist "%~dp0start_hidden.vbs" (
  echo [!] start_hidden.vbs is missing
  pause
  exit /b 1
)

wscript.exe "%~dp0start_hidden.vbs"
ping -n 4 127.0.0.1 >nul

powershell -NoProfile -Command "if (Get-Process python -ErrorAction SilentlyContinue) { Write-Host '[OK] Running in background, no window. Needs about 20s to connect.' -ForegroundColor Green } else { Write-Host '[!] Not running. Check aemeath.log' -ForegroundColor Red }"

ping -n 6 127.0.0.1 >nul
