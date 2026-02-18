@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Запуск Telegram-бота "ИИ-психолог"...
echo Остановка: закройте окно или Ctrl+C в терминале.
echo.

python bot.py

if errorlevel 1 (
    echo.
    echo Ошибка запуска. Проверьте:
    echo - установлен ли Python 3.10+ и зависимости: pip install -r requirements.txt
    echo - создан ли файл .env с TELEGRAM_BOT_TOKEN и DEEPSEEK_API_KEY
    echo Подробнее: INSTRUCTIONS.md
    pause
)
