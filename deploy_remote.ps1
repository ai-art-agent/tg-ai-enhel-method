# Remote: SSH to VM, pull and restart (window stays open)
$ErrorActionPreference = "Continue"
$keyPath = Join-Path $env:USERPROFILE ".ssh\id_ed25519_yandex"
$hostUser = "enhel-method@158.160.169.204"
$remoteCmd = "cd ~/tg-ai-enhel-method && git pull && venv/bin/pip install -r requirements.txt && sudo systemctl restart tg-ai-enhel-method && (sudo systemctl restart robokassa-server 2>/dev/null || true) && echo '' && echo '--- Service status (bot) ---' && sudo systemctl status tg-ai-enhel-method --no-pager && echo '' && echo '--- Robokassa service (if present) ---' && (sudo systemctl status robokassa-server --no-pager 2>/dev/null || echo 'robokassa-server not configured')"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Window 2: VM - git pull and restart" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

& ssh -i $keyPath $hostUser $remoteCmd

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Done. Press Enter to close." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Read-Host "Press Enter to close"
