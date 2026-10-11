@echo off
setlocal
set "AUTOZZZ_LAUNCHER=%~dp0Start-AutoZZZ.cmd"
if not exist "%AUTOZZZ_LAUNCHER%" (
    echo Start-AutoZZZ.cmd was not found beside this launcher.
    pause
    exit /b 1
)
powershell.exe -NoProfile -Command "try { Start-Process -FilePath $env:AUTOZZZ_LAUNCHER -Verb RunAs -WindowStyle Hidden -ErrorAction Stop } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
    echo Administrator launch was cancelled or failed.
    pause
    exit /b 1
)
exit /b 0
