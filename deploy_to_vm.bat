@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Открыть первое окно PowerShell (локальные команды). Оно не закрывается и по окончании откроет второе окно (ВМ).
start "Локально — коммит и push" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0deploy_local.ps1"
