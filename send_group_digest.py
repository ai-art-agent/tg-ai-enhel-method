#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Дайджест по оплатившим групповые занятия: выборка из БД за последние N часов (МСК),
таблица в Telegram на указанный chat_id.

Режимы (GROUP_DIGEST_MODE в .env):
  - immediate — уведомления уходят сразу при каждой оплате (из ResultURL); этот скрипт по cron не нужен.
  - scheduled — отправка по расписанию в моменты GROUP_DIGEST_TIME_1/2/3 (пустые слоты пропускаются, от 1 до 3 раз в сутки).

Запуск (для режима scheduled):
  python send_group_digest.py [--since-hours 12]
  По умолчанию --since-hours берётся из GROUP_DIGEST_SINCE_HOURS или 12.

В режиме scheduled скрипт при запуске проверяет текущее время (МСК): если оно совпадает с одним из
заданных непустых GROUP_DIGEST_TIME_* (формат HH:MM), отправляет дайджест; иначе выходит без отправки.
Cron лучше запускать каждые 5–10 минут; отправка произойдёт только в заданные часы.

Требуется в .env: TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_NOTIFY_CHAT_ID, PAYMENTS_DB_PATH;
для scheduled — хотя бы один из GROUP_DIGEST_TIME_1, GROUP_DIGEST_TIME_2, GROUP_DIGEST_TIME_3.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

# родительская папка в пути, чтобы подтянуть robokassa_integration
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from robokassa_integration import PaymentsDB, _parse_notify_chat_id

MSK = ZoneInfo("Europe/Moscow")


def format_digest(rows: list[dict]) -> str:
    """Форматирует список заказов в текстовую таблицу. Даты в МСК."""
    if not rows:
        return "За выбранный период оплат по групповым нет."
    lines = ["Дата и время (МСК) | user_id | chat_id | Тариф | Сумма"]
    for r in rows:
        paid_at = r.get("paid_at")
        if paid_at:
            dt = datetime.fromtimestamp(paid_at, tz=timezone.utc).astimezone(MSK)
            time_str = dt.strftime("%d.%m.%Y %H:%M")
        else:
            time_str = "—"
        user_id = r.get("user_id") or "—"
        chat_id = r.get("chat_id") or "—"
        product = (r.get("product_code") or "").replace("group_", "").capitalize()
        if product == "Standard":
            product = "Стандарт"
        elif product == "Vip":
            product = "VIP"
        amount = r.get("amount") or "—"
        lines.append(f"{time_str} | {user_id} | {chat_id} | {product} | {amount} ₽")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Дайджест оплат по групповым занятиям в Telegram")
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="За сколько часов от текущего момента выбирать оплаты (по UTC). По умолчанию — из GROUP_DIGEST_SINCE_HOURS или 12.",
    )
    args = parser.parse_args()

    mode = (os.getenv("GROUP_DIGEST_MODE") or "scheduled").strip().lower()
    if mode == "immediate":
        print("Режим GROUP_DIGEST_MODE=immediate: уведомления отправляются при каждой оплате, скрипт не нужен.", file=sys.stderr)
        sys.exit(0)

    # Режим scheduled: только если текущее время (МСК) совпадает с одним из заданных слотов
    time1 = (os.getenv("GROUP_DIGEST_TIME_1") or "").strip()
    time2 = (os.getenv("GROUP_DIGEST_TIME_2") or "").strip()
    time3 = (os.getenv("GROUP_DIGEST_TIME_3") or "").strip()
    scheduled_times = [t for t in (time1, time2, time3) if t]
    # Нормализуем HH:MM (допускаем H:MM; секунды отбрасываем)
    def norm(s: str) -> str:
        s = s.strip()
        parts = s.split(":")
        if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        return s
    scheduled_times = [norm(t) for t in scheduled_times]
    if not scheduled_times:
        print("Режим scheduled: не задано ни одного времени (GROUP_DIGEST_TIME_1/2/3). Выход.", file=sys.stderr)
        sys.exit(0)
    now_msk = datetime.now(MSK)
    current_slot = now_msk.strftime("%H:%M")
    if current_slot not in scheduled_times:
        sys.exit(0)

    since_hours = args.since_hours
    if since_hours is None:
        try:
            since_hours = float((os.getenv("GROUP_DIGEST_SINCE_HOURS") or "12").strip())
        except ValueError:
            since_hours = 12.0

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id_str = (os.getenv("TELEGRAM_GROUP_NOTIFY_CHAT_ID") or "").strip()
    if not token:
        print("Ошибка: не задан TELEGRAM_BOT_TOKEN в .env", file=sys.stderr)
        sys.exit(1)
    if not chat_id_str:
        print("Ошибка: не задан TELEGRAM_GROUP_NOTIFY_CHAT_ID в .env", file=sys.stderr)
        sys.exit(1)
    chat_id = _parse_notify_chat_id(chat_id_str)
    if chat_id is None:
        print("Ошибка: TELEGRAM_GROUP_NOTIFY_CHAT_ID задан некорректно (ожидается число или @username).", file=sys.stderr)
        sys.exit(1)

    db = PaymentsDB.from_env()
    since_ts = int(datetime.now(timezone.utc).timestamp() - since_hours * 3600)
    rows = db.get_group_orders_paid_since(since_ts)

    now_msk_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    title = f"Групповые занятия: оплаты за последние {int(since_hours)} ч (на {now_msk_str} МСК)"
    body = format_digest(rows)
    text = f"{title}\n\n<pre>{body}</pre>"

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode(
            {
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
            }
        ).encode("utf-8")
        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urlopen(req, timeout=15) as resp:
            resp.read()
        print(f"Отправлено: {len(rows)} записей в chat_id={chat_id}")
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
