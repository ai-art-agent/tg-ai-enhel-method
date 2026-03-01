# -*- coding: utf-8 -*-
"""
Локальная проверка диалога бота без Telegram.
Запуск: python test_dialog.py (из папки проекта, с настроенным .env и DEEPSEEK_API_KEY).

Команды:
  new, сброс, /new  — начать новый диалог (очистить историю).
  exit, quit, /exit  — выход из скрипта.

В остальных случаях введённая строка отправляется боту как сообщение пользователя;
ответ печатается в консоль (с валидатором, как в боте).
"""

import asyncio
import sys


async def main() -> None:
    from bot import get_bot_reply, clear_history

    TEST_USER_ID = 0
    last_buttons = []
    print("Локальный диалог с ботом (без Telegram).")
    print("Команды: new / сброс — новый диалог; 1, 2, … — нажать кнопку; exit — выход.")
    print("—" * 40)

    while True:
        try:
            line = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break
        if not line:
            continue
        if line.lower() in ("new", "сброс", "/new", "н"):
            clear_history(TEST_USER_ID)
            last_buttons = []
            print("[ Диалог сброшен. Начните с «Начать» или «Начать диагностику». ]\n")
            continue
        if line.lower() in ("exit", "quit", "/exit", "q", "выход"):
            print("Выход.")
            break
        if line.isdigit() and last_buttons and 1 <= int(line) <= len(last_buttons):
            label, callback = last_buttons[int(line) - 1]
            line = callback or label
        last_buttons = []

        print("Бот: ", end="", flush=True)
        try:
            reply, buttons, _, _, _ = await get_bot_reply(TEST_USER_ID, line, context=None)
            last_buttons = buttons or []
            print(reply or "(пусто)")
            if last_buttons:
                print("  Кнопки:", " | ".join(f"{i+1}) {b[0]}" for i, b in enumerate(last_buttons)))
        except Exception as e:
            print(f"\nОшибка: {e}")
            import traceback
            traceback.print_exc()
        print()


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
