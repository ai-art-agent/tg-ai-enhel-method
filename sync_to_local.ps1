# Скопировать изменённые файлы из текущей папки (worktree) в локальный репозиторий на компе.
# Запускать из папки wcf (worktree): pwsh -ExecutionPolicy Bypass -File sync_to_local.ps1
$ErrorActionPreference = "Stop"
$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetDir = "C:\Users\AI_Art\telegram-ai-psychologist"

if (-not (Test-Path $TargetDir)) {
    Write-Host "Папка не найдена: $TargetDir" -ForegroundColor Red
    exit 1
}

$Files = @(
    "bot.py",
    "system_prompt.txt",
    "deploy_to_vm.bat",
    "deploy_local.ps1",
    "sync_to_local.ps1",
    "sync_to_local.bat"
)

Write-Host "Копирование из worktree в локальный репозиторий..." -ForegroundColor Cyan
Write-Host "  Источник: $SourceDir" -ForegroundColor Gray
Write-Host "  Назначение: $TargetDir" -ForegroundColor Gray
Write-Host ""

foreach ($f in $Files) {
    $src = Join-Path $SourceDir $f
    $dst = Join-Path $TargetDir $f
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $dst -Force
        Write-Host "  OK: $f" -ForegroundColor Green
    } else {
        Write-Host "  Пропуск (нет файла): $f" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Готово. Локальные файлы обновлены." -ForegroundColor Cyan
