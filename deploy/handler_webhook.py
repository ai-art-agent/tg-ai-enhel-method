# -*- coding: utf-8 -*-
"""
Обработчик для Yandex Cloud Functions (режим webhook).
Точка входа: handler(event, context).

В настройках функции укажите:
  Точка входа: deploy.handler_webhook.handler
  Переменные окружения: TELEGRAM_BOT_TOKEN, DEEPSEEK_API_KEY, при необходимости OPENAI_API_KEY
"""
import asyncio
import base64
import json
import logging
import os
import sys

# Добавляем корень проекта в путь (при деплое в функцию обычно кладут весь проект)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import process_webhook_update

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def handler(event, context):
    """
    Обработчик HTTP-триггера Yandex Cloud Functions.
    event["body"] — тело запроса от Telegram (JSON). Может быть в base64.
    """
    try:
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        if isinstance(body, bytes):
            body = body.decode("utf-8")

        # Запускаем асинхронную обработку в том же процессе
        asyncio.run(process_webhook_update(body))

        return {
            "statusCode": 200,
            "body": "ok",
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
        }
    except Exception as e:
        logging.exception("Webhook handler error: %s", e)
        return {
            "statusCode": 500,
            "body": "error",
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
        }
