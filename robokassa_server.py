from __future__ import annotations

"""
HTTP-сервер Robokassa для развёртывания на ВМ (Yandex Compute Cloud).

Эндпоинты:
  POST /robokassa/result  — ResultURL (server-to-server), возвращает "OK{InvId}" или "ERROR"
  GET  /robokassa/success — SuccessURL (редирект после оплаты)
  GET  /robokassa/fail    — FailURL (отмена/ошибка оплаты)

Запуск (в venv на ВМ, пример):
  uvicorn robokassa_server:app --host 0.0.0.0 --port 8000

В кабинете Robokassa:
  Result URL  = http://ВАШ_IP:8000/robokassa/result
  Success URL = http://ВАШ_IP:8000/robokassa/success
  Fail URL    = http://ВАШ_IP:8000/robokassa/fail

Документация: https://docs.robokassa.ru/ru/notifications-and-redirects
При фильтрации по IP разрешите: 185.59.216.65, 185.59.217.65
"""

import os
import logging
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from robokassa_integration import (
    PaymentsDB,
    RobokassaConfig,
    build_access_message,
    telegram_send_message,
    verify_result_url,
    verify_success_url,
)

logger = logging.getLogger("robokassa_server")
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

app = FastAPI()


async def _collect_params(request: Request) -> Dict[str, Any]:
    """
    Собираем параметры из query string и form-urlencoded body.
    Robokassa может отправлять данные и так, и так.
    """
    params: Dict[str, Any] = {}
    for k, v in request.query_params.multi_items():
        params[k] = v
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            form = await request.form()
            for k, v in form.multi_items():
                params[k] = v
        except Exception:
            # не form-data — можно игнорировать
            pass
    return params


@app.post("/robokassa/result")
async def robokassa_result(request: Request) -> PlainTextResponse:
    """
    ResultURL: подтверждение оплаты от Robokassa.
    Должен вернуть "OK{InvId}" при успешной проверке подписи.
    """
    try:
        cfg = RobokassaConfig.from_env()
        db = PaymentsDB.from_env()
        params = await _collect_params(request)
        parsed = verify_result_url(params, cfg=cfg)

        inv_id = int(parsed["inv_id"])
        out_sum = str(parsed["out_sum"])
        order = db.get_order(inv_id)
        if not order:
            logger.warning("Robokassa (VM): unknown InvId=%s", inv_id)
            return PlainTextResponse("ERROR")

        if str(order.get("amount")) != out_sum:
            logger.warning(
                "Robokassa (VM): amount mismatch InvId=%s %s != %s",
                inv_id,
                order.get("amount"),
                out_sum,
            )
            return PlainTextResponse("ERROR")

        shp = parsed.get("shp") or {}
        token_expected = str(order.get("order_token") or "")
        token_got = str(shp.get("Shp_order_token") or "")
        if token_expected and token_got and token_expected != token_got:
            logger.warning("Robokassa (VM): token mismatch InvId=%s", inv_id)
            return PlainTextResponse("ERROR")

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
                        logger.exception("Robokassa (VM): Telegram sendMessage failed: %s", e)

        return PlainTextResponse(f"OK{inv_id}")
    except Exception as e:
        logger.exception("Robokassa (VM): ResultURL error: %s", e)
        return PlainTextResponse("ERROR")


@app.get("/robokassa/success")
async def robokassa_success(request: Request) -> PlainTextResponse:
    """
    SuccessURL: редирект пользователя после оплаты.
    Это НЕ подтверждение оплаты — оно приходит на ResultURL.
    """
    try:
        cfg = RobokassaConfig.from_env()
        params = await _collect_params(request)
        _ = verify_success_url(params, cfg=cfg)
        return PlainTextResponse(
            "Оплата принята. Вернитесь в Telegram — бот пришлёт доступ.",
            media_type="text/plain; charset=utf-8",
        )
    except Exception as e:
        logger.exception("Robokassa (VM): SuccessURL error: %s", e)
        return PlainTextResponse(
            "Не удалось проверить оплату. Если деньги списались — напишите в поддержку.",
            media_type="text/plain; charset=utf-8",
        )


@app.get("/robokassa/fail")
async def robokassa_fail(request: Request) -> PlainTextResponse:  # noqa: ARG001
    return PlainTextResponse(
        "Оплата не завершена. Вы можете попробовать ещё раз в боте.",
        media_type="text/plain; charset=utf-8",
    )

