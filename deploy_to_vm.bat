@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Деплой на ВМ: коммит, push, обновление на сервере
echo ============================================
echo.

echo --- 1. Локальный репозиторий ---
git status
echo.

echo --- 2. Добавление файлов ---
git add .
git add bot.py
git add deploy/tg-ai-enhel-method.service
git add system_prompt.txt
git status
echo.

echo --- 3. Коммит (введите сообщение и нажмите Enter) ---
set /p COMMIT_MSG="Сообщение коммита (пусто = «Обновление промпта и кнопок бота»): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=Обновление промпта и кнопок бота
git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
    echo Коммит не создан (возможно, нечего коммитить или ошибка).
    set /p CONTINUE="Продолжить и перейти к push? (y/n): "
    if /i not "%CONTINUE%"=="y" exit /b 1
) else (
    echo Коммит создан.
)
echo.

echo --- 4. Отправка на GitHub и деплой на ВМ ---
set /p PUSH_NOW="Выполнить git push и обновление на ВМ? (y/n): "
if /i not "%PUSH_NOW%"=="y" (
    echo Выход без push.
    pause
    exit /b 0
)

echo.
echo Выполняю git push...
git push
if errorlevel 1 (
    echo Ошибка git push. Проверьте доступ к репозиторию.
    pause
    exit /b 1
)

echo.
echo Push выполнен. Подключаюсь к ВМ и обновляю бота...
echo.

ssh -i "%USERPROFILE%\.ssh\id_ed25519_yandex" enhel-method@158.160.169.204 "cd ~/tg-ai-enhel-method && git pull && venv/bin/pip install -r requirements.txt && sudo systemctl restart tg-ai-enhel-method && echo. && echo --- Статус службы --- && sudo systemctl status tg-ai-enhel-method --no-pager"

echo.
echo ============================================
echo   Готово.
echo ============================================
pause
