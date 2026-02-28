# Local: commit and push (window stays open)
$ErrorActionPreference = "Stop"
$ProjectPath = "C:\Users\AI_Art\telegram-ai-psychologist"
Set-Location $ProjectPath

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Window 1: Local - commit and push" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "--- 1. Repository status ---" -ForegroundColor Yellow
git status
Write-Host ""

Write-Host "--- 2. Add files ---" -ForegroundColor Yellow
git add .
git add bot.py
git add deploy/tg-ai-enhel-method.service
git add system_prompt.txt
git status
Write-Host ""

Write-Host "--- 3. Commit ---" -ForegroundColor Yellow
$defaultMsg = "Obnovlenie prompta i knopok bota"
$msg = Read-Host "Commit message (Enter = default)"
if ([string]::IsNullOrWhiteSpace($msg)) { $msg = $defaultMsg }
git commit -m $msg
if ($LASTEXITCODE -ne 0) {
    Write-Host "Commit skipped (nothing to commit or error)." -ForegroundColor Yellow
    $cont = Read-Host "Continue to push? (y/n)"
    if ($cont -ne "y" -and $cont -ne "Y") { exit 1 }
}
Write-Host ""

Write-Host "--- 4. Push and open VM window ---" -ForegroundColor Yellow
$push = Read-Host "Run git push and open VM window? (y/n)"
if ($push -ne "y" -and $push -ne "Y") {
    Write-Host "Exiting without push."
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host ""
Write-Host "Running git push..." -ForegroundColor Green
git push
if ($LASTEXITCODE -ne 0) {
    Write-Host "git push failed. Check repo access." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host ""
Write-Host "Push done. Opening VM window..." -ForegroundColor Green
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pwshExe = "C:\Program Files\PowerShell\7-preview\pwsh.exe"
Start-Process $pwshExe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptDir "deploy_remote.ps1")

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Local part done. Press Enter to close." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Read-Host "Press Enter to close"
