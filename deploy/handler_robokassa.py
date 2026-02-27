# -*- coding: utf-8 -*-
"""
Обработчики для Robokassa под Yandex Cloud Functions (HTTP).

Сделано 3 точки входа (entrypoint):
  - deploy.handler_robokassa.handler_result  — ResultURL (server-to-server)
  - deploy.handler_robokassa.handler_success — SuccessURL (редирект пользователя)
  - deploy.handler_robokassa.handler_fail    — FailURL (редирект пользователя)

ResultURL ОБЯЗАТЕЛЕН: именно он подтверждает оплату. В ответ нужно вернуть "OK{InvId}".
"""

import base64
import json
import logging
import os
import sys
from urllib.parse import parse_qs

# Добавляем корень проекта в путь (как в handler_webhook.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robokassa_integration import (  # noqa: E402
    PaymentsDB,
    RobokassaConfig,
    build_access_message,
    telegram_send_message,
    verify_result_url,
    verify_success_url,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def _collect_params(event: dict) -> dict:
    params: dict = {}

    # query string
    q = event.get("queryStringParameters") or {}
    if isinstance(q, dict):
        params.update(q)

    # body может быть form-urlencoded
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    body = body.strip()
    if body:
        parsed = parse_qs(body, keep_blank_values=True)
        for k, v in parsed.items():
            if not v:
                continue
            params[k] = v[0]

    return params


def handler_result(event, context):
    """
    ResultURL (server-to-server). Должен вернуть "OK{InvId}".
    """
    try:
        cfg = RobokassaConfig.from_env()
        db = PaymentsDB.from_env()
        params = _collect_params(event)
        parsed = verify_result_url(params, cfg=cfg)

        inv_id = int(parsed["inv_id"])
        out_sum = str(parsed["out_sum"])
        order = db.get_order(inv_id)
        if not order:
            logging.warning("Robokassa: unknown InvId=%s", inv_id)
            return {"statusCode": 200, "body": "ERROR"}

        if str(order.get("amount")) != out_sum:
            logging.warning("Robokassa: amount mismatch InvId=%s %s != %s", inv_id, order.get("amount"), out_sum)
            return {"statusCode": 200, "body": "ERROR"}

        shp = parsed.get("shp") or {}
        token_expected = str(order.get("order_token") or "")
        token_got = str(shp.get("Shp_order_token") or "")
        if token_expected and token_got and token_expected != token_got:
            logging.warning("Robokassa: token mismatch InvId=%s", inv_id)
            return {"statusCode": 200, "body": "ERROR"}

        newly_paid = db.mark_paid_if_pending(inv_id, raw_params=parsed.get("raw") or {})
        if newly_paid:
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or ""
            if bot_token:
                chat_id = int(order.get("chat_id") or shp.get("Shp_chat_id") or 0)
                if chat_id:
                    text = build_access_message(str(order.get("product_code") or ""))
                    try:
                        telegram_send_message(
                            bot_token=bot_token,
                            chat_id=chat_id,
                            text=text,
                            disable_web_preview=True,
                        )
                    except Exception as e:
                        logging.exception("Telegram sendMessage failed: %s", e)

        return {"statusCode": 200, "body": f"OK{inv_id}"}
    except Exception as e:
        logging.exception("Robokassa ResultURL error: %s", e)
        return {"statusCode": 200, "body": "ERROR"}


def handler_success(event, context):
    """
    SuccessURL (редирект пользователя после оплаты).
    Это НЕ подтверждение оплаты, подтверждение приходит на ResultURL.
    """
    try:
        cfg = RobokassaConfig.from_env()
        params = _collect_params(event)
        _ = verify_success_url(params, cfg=cfg)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": "Оплата принята. Вернитесь в Telegram — бот пришлёт доступ.",
        }
    except Exception as e:
        logging.exception("Robokassa SuccessURL error: %s", e)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": "Не удалось проверить оплату. Если деньги списались — напишите в поддержку.",
        }


def handler_fail(event, context):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/plain; charset=utf-8"},
        "body": "Оплата не завершена. Вы можете попробовать ещё раз в боте.",
    }

