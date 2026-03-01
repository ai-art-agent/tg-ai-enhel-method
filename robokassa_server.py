from __future__ import annotations

"""
HTTP-сервер Robokassa для развёртывания на ВМ (Yandex Compute Cloud).

Эндпоинты:
  GET/POST /robokassa/result — ResultURL (server-to-server); метод задаётся в настройках магазина Робокассы. Возвращает "OK{InvId}" или "ERROR"
  GET  /robokassa/success — SuccessURL (редирект после оплаты)
  GET  /robokassa/fail    — FailURL (отмена/ошибка оплаты)

Запуск (в venv на ВМ, пример):
  uvicorn robokassa_server:app --host 0.0.0.0 --port 8000 --http h11

  Параметр --http h11 использует парсер h11 вместо httptools; если в логах
  есть «Invalid HTTP request received» при вызовах от Робокассы, h11 часто
  принимает такие запросы и тогда в логе появится строка GET /robokassa/result.

В кабинете Robokassa:
  Result URL  = http://ВАШ_IP:8000/robokassa/result
  Success URL = http://ВАШ_IP:8000/robokassa/success
  Fail URL    = http://ВАШ_IP:8000/robokassa/fail

На ВМ в .env задайте TELEGRAM_BOT_USERNAME (без @), например MyPsychologistBot,
чтобы на страницах успеха/ошибки показывалась кнопка «Открыть чат» и на мобильных
выполнялся переход в приложение Telegram.

Документация: https://docs.robokassa.ru/ru/notifications-and-redirects
При фильтрации по IP разрешите: 185.59.216.65, 185.59.217.65

Тестовый режим (IsTest=1): на тестовой странице оплаты выберите блок
«Успешное проведение платежа» — иначе попадёте на Fail URL и ResultURL не вызовется.
"""

import os
import time
import json
import logging
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse

from robokassa_integration import (
    PaymentsDB,
    RobokassaConfig,
    build_access_message,
    telegram_send_message,
    verify_result_url,
    verify_success_url,
    send_group_payment_notify_immediate,
)

logger = logging.getLogger("robokassa_server")
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

app = FastAPI()


@app.middleware("http")
async def log_robokassa_requests(request: Request, call_next):
    """Логирует каждый запрос к /robokassa/* с IP — чтобы видеть вызовы от Робокассы (185.59.216.65)."""
    if request.url.path.startswith("/robokassa/"):
        client = request.client
        host = client.host if client else request.headers.get("x-forwarded-for", "?")
        logger.info("Robokassa request: %s %s from %s", request.method, request.url.path, host)
    return await call_next(request)


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


def _bot_open_link() -> str:
    """Ссылка для открытия чата (t.me/username). Пустая строка, если username не задан."""
    username = (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    if not username:
        return ""
    return f"https://t.me/{username}"


def _success_html() -> str:
    link = _bot_open_link()
    if link:
        # username для tg://resolve?domain=... (на мобильных открывает приложение)
        username = link.rstrip("/").split("/")[-1] if link else ""
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Оплата принята</title>
  <style>
    body {{ font-family: system-ui, sans-serif; text-align: center; padding: 2rem; margin: 0; }}
    .msg {{ margin-bottom: 1.5rem; color: #333; }}
    a.btn {{ display: inline-block; padding: 12px 24px; background: #0088cc; color: #fff; text-decoration: none; border-radius: 8px; font-size: 1.1rem; }}
    a.btn:hover {{ background: #006699; }}
  </style>
  <script>
    (function() {{
      var username = {json.dumps(username)};
      var isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
      if (isMobile && username) {{
        setTimeout(function() {{ window.location.href = 'tg://resolve?domain=' + username; }}, 1500);
      }}
    }})();
  </script>
</head>
<body>
  <p class="msg">Оплата принята. Ваш доступ уже отправлен — откройте чат, чтобы получить его.</p>
  <p><a class="btn" href="{link}">Открыть чат</a></p>
</body>
</html>"""
    return """<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Оплата принята</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:2rem"><p>Оплата принята. Ваш доступ уже отправлен.</p></body>
</html>"""


def _fail_html() -> str:
    link = _bot_open_link()
    if link:
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Оплата не завершена</title>
  <style>
    body {{ font-family: system-ui, sans-serif; text-align: center; padding: 2rem; margin: 0; }}
    .msg {{ margin-bottom: 1.5rem; color: #333; }}
    a.btn {{ display: inline-block; padding: 12px 24px; background: #0088cc; color: #fff; text-decoration: none; border-radius: 8px; font-size: 1.1rem; }}
    a.btn:hover {{ background: #006699; }}
  </style>
</head>
<body>
  <p class="msg">Оплата не завершена. Вы можете попробовать ещё раз.</p>
  <p><a class="btn" href="{link}">Вернуться в чат</a></p>
</body>
</html>"""
    return """<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Оплата не завершена</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:2rem"><p>Оплата не завершена. Вы можете попробовать ещё раз.</p></body>
</html>"""


@app.api_route("/robokassa/result", methods=["GET", "POST"])
async def robokassa_result(request: Request) -> PlainTextResponse:
    """
    ResultURL: подтверждение оплаты от Robokassa.
    Должен вернуть "OK{InvId}" при успешной проверке подписи.
    """
    params = await _collect_params(request)
    # #region agent log
    try:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug-15b236.log")
        with open(_log_path, "a", encoding="utf-8") as _f:
            _f.write(
                json.dumps(
                    {
                        "id": "robokassa_result_called",
                        "timestamp": time.time(),
                        "location": "robokassa_server.robokassa_result",
                        "message": "ResultURL hit",
                        "data": {"InvId": params.get("InvId"), "OutSum": params.get("OutSum")},
                        "hypothesisId": "H3",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    try:
        cfg = RobokassaConfig.from_env()
        db = PaymentsDB.from_env()
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
                try:
                    send_group_payment_notify_immediate(bot_token, db.get_order(inv_id))
                except Exception as e:
                    logger.exception("Robokassa (VM): group digest immediate notify failed: %s", e)

        return PlainTextResponse(f"OK{inv_id}")
    except Exception as e:
        logger.exception("Robokassa (VM): ResultURL error: %s", e)
        return PlainTextResponse("ERROR")


@app.get("/robokassa/success")
async def robokassa_success(request: Request) -> HTMLResponse:
    """
    SuccessURL: редирект пользователя после оплаты.
    Это НЕ подтверждение оплаты — оно приходит на ResultURL.
    """
    try:
        cfg = RobokassaConfig.from_env()
        params = await _collect_params(request)
        _ = verify_success_url(params, cfg=cfg)
        return HTMLResponse(_success_html())
    except Exception as e:
        logger.exception("Robokassa (VM): SuccessURL error: %s", e)
        return HTMLResponse(
            """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:2rem">
  <p>Не удалось проверить оплату. Если деньги списались — напишите в поддержку.</p>
</body></html>"""
        )


@app.get("/robokassa/fail")
async def robokassa_fail(request: Request) -> HTMLResponse:  # noqa: ARG001
    return HTMLResponse(_fail_html())

