@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Открыть первое окно PowerShell 7 (локальные команды). Оно не закрывается и по окончании откроет второе окно (ВМ).
start "Локально — коммит и push" "C:\Program Files\PowerShell\7-preview\pwsh.exe" -NoExit -ExecutionPolicy Bypass -File "%~dp0deploy_local.ps1"
