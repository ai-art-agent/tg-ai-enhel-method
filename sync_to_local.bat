@echo off
chcp 65001 >nul
cd /d "%~dp0"
"C:\Program Files\PowerShell\7-preview\pwsh.exe" -NoExit -ExecutionPolicy Bypass -File "%~dp0sync_to_local.ps1"
