# -*- coding: utf-8 -*-
"""
Telegram-бот «ИИ-психолог» с ответами через DeepSeek API.
Поддержка: текст, голосовые (Whisper), потоковый вывод.
Перед запуском: заполните .env (TELEGRAM_BOT_TOKEN, DEEPSEEK_API_KEY; для голоса — OPENAI_API_KEY).
Подробно: INSTRUCTIONS.md.
"""

import os
import re
import html
import logging
import tempfile
import asyncio
from collections import defaultdict
from typing import Optional

from robokassa_integration import (
    PaymentsDB,
    RobokassaConfig,
    build_payment_url,
    _to_amount_str,
)

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from openai import AsyncOpenAI
from openai import APIStatusError

# ============== НАСТРОЙКИ (уточните под свои ответы из INSTRUCTIONS.md) ==============

# Имя и описание бота (Этап 3)
BOT_NAME = "ИИ-психолог"
BOT_DESCRIPTION = "Вижу, что ты хочешь поговорить. Я здесь, чтобы выслушать и поддержать. Помни: я не заменяю живого специалиста."

# Путь к файлу с системным промптом (рядом с bot.py).
_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")


def _load_system_prompt() -> str:
    """Загружает системный промпт из файла system_prompt.txt."""
    try:
        with open(_PROMPT_PATH, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            raise ValueError("Файл system_prompt.txt пуст.")
        return content
    except FileNotFoundError:
        raise ValueError(
            f"Не найден файл с промптом: {_PROMPT_PATH}. "
            "Положите system_prompt.txt в папку с bot.py."
        )
    except OSError as e:
        raise ValueError(f"Не удалось прочитать system_prompt.txt: {e}") from e


SYSTEM_PROMPT = _load_system_prompt()

# История диалога: сколько последних пар сообщений хранить (Этап 4). 0 = не хранить.
MAX_HISTORY_MESSAGES = 10

# Максимальная длина ответа ИИ в символах (Этап 2). 0 = без жёсткого лимита.
MAX_RESPONSE_LENGTH = 0

# Текст согласия при /start (Этап 4). Пустая строка = не показывать. Без упоминания бота/ИИ — в соответствии с промптом.
START_DISCLAIMER = "Каждый вопрос, каждая проблема уникальны и требуют индивидуального подхода. Именно поэтому я здесь, чтобы помочь тебе разобраться в своем состоянии и найти решение."

# Контакты поддержки для /support (Этап 4). Оставьте пустым, если команда не нужна.
SUPPORT_TEXT = """При кризисе или тяжёлом состоянии важно обратиться к человеку:
• Телефон доверия: 8-800-2000-122 (бесплатно, Россия)
• Психологическая помощь: ищите службы в своём городе."""

# Политика конфиденциальности для /privacy (Этап 6). Кратко.
PRIVACY_TEXT = "Сообщения обрабатываются для ответа ИИ и не передаются третьим лицам. Мы не храним переписку для аналитики."

# Разрешённые user_id (Этап 6). Пустой список = доступ у всех. Иначе только эти id.
ALLOWED_USER_IDS = []  # Пример: [123456789, 987654321]

# Логирование в файл (Этап 5). True = писать в bot.log.
LOG_TO_FILE = False

# Модель DeepSeek (Этап 2): "deepseek-chat" или "deepseek-reasoner"
DEEPSEEK_MODEL = "deepseek-chat"

# Потоковый вывод ответа (Этап 2). True = ответ печатается по частям.
STREAM_RESPONSE = True

# Голосовые сообщения: транскрипция через OpenAI Whisper. Нужен OPENAI_API_KEY в .env.
VOICE_ENABLED = True

# Кнопки по шагам диалога: ключ = step_id из тега [STEP:step_id] в ответе модели.
STEP_KEYBOARDS = {
    "start_diagnosis": [
        [("Начать диагностику", "Начать диагностику")],
    ],
    "form_address": [
        [("Женщина", "Женская форма обращения"), ("Мужчина", "Мужская форма обращения"), ("Нейтральная", "Нейтральная форма обращения")],
    ],
    "messenger": [
        [("Telegram", "Telegram"), ("Сотовый", "Сотовый"), ("Другое", "Другое")],
    ],
    "conflict": [
        [("Вариант 1", "Вариант 1")],
        [("Вариант 2", "Вариант 2")],
        [("Вариант 3", "Вариант 3")],
        [("Свой вариант", "Свой вариант")],
    ],
    "insight_next": [
        [("Обсудить возможные пути", "Обсудить возможные пути")],
    ],
    "readiness": [
        [("Готов/готова", "Готов/готова"), ("Еще подумаю", "Еще подумаю")],
    ],
    "products": [
        [("Групповые занятия", "Групповые занятия"), ("Онлайн вебинар", "Онлайн вебинар")],
        [("AI-Психолог Pro", "AI-Психолог Pro")],
    ],
    "vip": [
        [("VIP", "VIP")],
        [("Стандарт", "Стандарт")],
    ],
    "pay_choice": [
        [("Оплатить", "Оплатить"), ("Еще думаю", "Еще думаю")],
    ],
    "webinar_offer": [
        [("Онлайн вебинар", "Онлайн вебинар")],
    ],
}

# Кнопки продуктов (callback_data) -> внутренний код продукта для платежей
PRODUCT_BUTTON_TO_CODE = {
    "Групповые занятия": "group",
    "Онлайн вебинар": "webinar",
    "AI-Психолог Pro": "pro",
}

def _amount_from_env(name: str, default: str) -> str:
    v = os.getenv(name, default)
    try:
        return _to_amount_str(v)
    except Exception:
        return _to_amount_str(default)


# Цены (можно переопределить переменными окружения)
#PRICE_GROUP_RUB = _amount_from_env("PRICE_GROUP_RUB", "29990") группа исключена
PRICE_GROUP_STANDARD_RUB = _amount_from_env("PRICE_GROUP_STANDARD_RUB", "24990")
PRICE_GROUP_VIP_RUB = _amount_from_env("PRICE_GROUP_VIP_RUB", "45990")
PRICE_WEBINAR_RUB = _amount_from_env("PRICE_WEBINAR_RUB", "2990")
PRICE_PRO_RUB = _amount_from_env("PRICE_PRO_RUB", "990")

PRODUCTS = {
    "group_standard": {
        "amount": PRICE_GROUP_STANDARD_RUB,
        "description": "Оплата: Групповые занятия (Стандарт)",
    },
    "group_vip": {
        "amount": PRICE_GROUP_VIP_RUB,
        "description": "Оплата: Групповые занятия (VIP)",
    },
    "webinar": {
        "amount": PRICE_WEBINAR_RUB,
        "description": "Оплата: Онлайн вебинар",
    },
    "pro": {
        "amount": PRICE_PRO_RUB,
        "description": "Оплата: AI-Психолог Pro (месяц)",
    },
}

# Формат анкеты (outcome) — совпадает с system_prompt.txt. При сохранении анкет/БД клиентов
# использовать те же ключи: readiness, product, tariff, preferred_contact_time, preferred_group_start.

# Парсинг тега [STEP:step_id] в конце ответа модели.
# Допускаем пробелы/перенос строки до и после тега в конце сообщения.
STEP_TAG_REGEX = re.compile(r"\s*\[STEP:(\w+)\]\s*$", re.IGNORECASE)
# Автогенерация кнопок: [BUTTONS: Текст1 | Текст2 | Текст3] (до 4 кнопок, до 64 байт на callback_data).
BUTTONS_TAG_REGEX = re.compile(r"\s*\[BUTTONS:\s*([^\]]+)\]", re.IGNORECASE)
CALLBACK_DATA_MAX_BYTES = 64

# Маркер списка вместо "*" / "-" (модель часто выводит Markdown, в Telegram без parse_mode они видны как символы).
LIST_MARKER = "➖"

# ============== КОД БОТА ==============

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("В .env не указан TELEGRAM_BOT_TOKEN. См. INSTRUCTIONS.md, Этап 1.")
if not DEEPSEEK_API_KEY:
    raise ValueError("В .env не указан DEEPSEEK_API_KEY. См. INSTRUCTIONS.md, Этап 2.")

# DeepSeek API (совместим с OpenAI SDK) — асинхронный клиент для потокового вывода
client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)
# OpenAI — только для Whisper (голосовые). Если ключа нет, голос отключён.
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
user_history = defaultdict(list)


def _format_reply_for_telegram(text: str) -> tuple[str, Optional[str]]:
    """
    Приводит ответ модели к виду для Telegram:
    - «**текст**» → жирный через HTML <b>, остальное экранируется для HTML.
    - Строки списков «* пункт» / «- пункт» → «➖ пункт».
    Возвращает (итоговый текст, parse_mode или None). parse_mode="HTML" при наличии тегов.
    """
    if not text:
        return text, None
    # Списки: в начале строки * или - с пробелом → маркер ➖
    text = re.sub(r"^(\s*)(\*|-)\s+", rf"\1{LIST_MARKER} ", text, flags=re.MULTILINE)
    # Жирный: **...** → <b>...</b> с экранированием содержимого и остального текста
    parts = re.split(r"\*\*(.+?)\*\*", text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            result.append(html.escape(part))
        else:
            result.append("<b>" + html.escape(part) + "</b>")
    out = "".join(result)
    # Если не было ни одного **, split вернул один элемент и тегов <b> нет — parse_mode не нужен
    use_html = "<b>" in out
    return (out, "HTML" if use_html else None)


def _get_reply_target(update: Update):
    """Сообщение, в ответ на которое шлём ответ (при тексте/голосе — message, при нажатии кнопки — callback.message)."""
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None


def _parse_step_from_reply(reply: str) -> tuple[str, Optional[str]]:
    """Убирает из ответа тег [STEP:step_id] в конце и возвращает (очищенный текст, step_id или None)."""
    m = STEP_TAG_REGEX.search(reply)
    if m:
        return reply[: m.start()].rstrip(), m.group(1).lower()
    return reply, None


def _keyboard_for_step(step_id: str) -> Optional[InlineKeyboardMarkup]:
    """Клавиатура по step_id; None если шаг неизвестен."""
    rows = STEP_KEYBOARDS.get(step_id)
    if not rows:
        return None
    keyboard = [[InlineKeyboardButton(str(label), callback_data=str(cb)) for label, cb in row] for row in rows]
    return InlineKeyboardMarkup(keyboard)


def _truncate_callback_data(s: str, max_bytes: int = CALLBACK_DATA_MAX_BYTES) -> str:
    """Обрезает строку до max_bytes в UTF-8 (лимит Telegram для callback_data)."""
    data = s.strip().encode("utf-8")
    if len(data) <= max_bytes:
        return s.strip()
    return data[:max_bytes].decode("utf-8", errors="ignore").strip() or s[:1]


def _parse_custom_buttons(reply: str) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Ищет в ответе тег [BUTTONS: Текст1 | Текст2 | ...], строит клавиатуру (до 4 кнопок),
    удаляет тег из текста. Возвращает (очищенный текст, клавиатура или None).
    """
    m = BUTTONS_TAG_REGEX.search(reply)
    if not m:
        return reply, None
    raw = m.group(1).strip()
    labels = [part.strip() for part in re.split(r"\s*\|\s*", raw) if part.strip()][:4]
    if not labels:
        return reply[: m.start()].rstrip() + reply[m.end() :].lstrip(), None
    rows = [[(label, _truncate_callback_data(label))] for label in labels]
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(str(label), callback_data=cb) for label, cb in row] for row in rows])
    cleaned = (reply[: m.start()].rstrip() + " " + reply[m.end() :].lstrip()).strip()
    return cleaned, keyboard


def get_history_messages(user_id: int) -> list[dict]:
    """Возвращает список сообщений для API OpenAI в формате role/content."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in user_history[user_id]:
        messages.append({"role": item["role"], "content": item["content"]})
    return messages


def add_to_history(user_id: int, role: str, content: str) -> None:
    user_history[user_id].append({"role": role, "content": content})
    if MAX_HISTORY_MESSAGES > 0:
        while len(user_history[user_id]) > MAX_HISTORY_MESSAGES * 2:
            user_history[user_id].pop(0)


def clear_history(user_id: int) -> None:
    user_history[user_id].clear()


def truncate_response(text: str) -> str:
    if MAX_RESPONSE_LENGTH <= 0:
        return text
    if len(text) <= MAX_RESPONSE_LENGTH:
        return text
    return text[: MAX_RESPONSE_LENGTH - 3].rstrip() + "..."


async def check_access(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    user_id = update.effective_user.id if update.effective_user else 0
    if user_id not in ALLOWED_USER_IDS:
        if update.message:
            await update.message.reply_text("Доступ к боту ограничен.")
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("Доступ ограничен.")
        return False
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update):
        return
    text = "Привет. Нажми кнопку ниже, чтобы начать разговор."
    if START_DISCLAIMER:
        text += "\n\n" + START_DISCLAIMER
    keyboard = [[InlineKeyboardButton("Начать", callback_data="start_chat")]]
    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update):
        return
    await update.message.reply_text(
        "Команды: /start — начало разговора, /help — эта справка."
        + (" /support — контакты поддержки." if SUPPORT_TEXT else "")
        + (" /privacy — конфиденциальность." if PRIVACY_TEXT else "")
        + (" /new — начать диалог заново (сбросить контекст)." if MAX_HISTORY_MESSAGES else "")
    )


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update):
        return
    if not SUPPORT_TEXT:
        await update.message.reply_text("Команда не настроена.")
        return
    await update.message.reply_text(SUPPORT_TEXT)


async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update):
        return
    if not PRIVACY_TEXT:
        await update.message.reply_text("Команда не настроена.")
        return
    await update.message.reply_text(PRIVACY_TEXT)


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сброс контекста диалога (команда /new)."""
    if not await check_access(update):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    clear_history(user_id)
    await update.message.reply_text("Контекст сброшен. Можешь начать разговор заново — напиши сообщение или нажми /start.")

async def button_new_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not await check_access(update):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await query.edit_message_text("Доступ ограничен.")
        return
    had_history = len(user_history[user_id]) > 0
    clear_history(user_id)
    if had_history:
        await query.edit_message_text("Контекст сброшен. Можешь начать новый разговор.")
    else:
        await query.edit_message_text("История пуста. Напиши сообщение — и мы начнём.")


async def button_start_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Начать» при /start — запускает первый ответ бота (как если бы пользователь написал «Начать»)."""
    if not update.callback_query:
        return
    await update.callback_query.answer()
    if not await check_access(update):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return
    await _reply_to_user(update, context, user_id, "Начать")


async def handle_step_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатия кнопки шага: callback_data уходит в модель как ответ пользователя."""
    if not update.callback_query:
        return
    if not await check_access(update):
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    user_text = (update.callback_query.data or "").strip()
    if not user_text:
        return

    # Запоминаем выбранный продукт, чтобы "Оплатить" мог выдать правильную ссылку.
    if user_text in PRODUCT_BUTTON_TO_CODE:
        context.user_data["selected_product"] = PRODUCT_BUTTON_TO_CODE[user_text]

    # При выборе групповых занятий запоминаем тариф (VIP / Стандарт).
    if user_text == "VIP" and context.user_data.get("selected_product") == "group":
        context.user_data["group_tariff"] = "vip"
    elif user_text == "Стандарт" and context.user_data.get("selected_product") == "group":
        context.user_data["group_tariff"] = "standard"

    # Специальная обработка оплаты (не отправляем это в модель).
    if user_text.lower() == "оплатить":
        await send_payment_link(update, context)
        return

    await _reply_to_user(update, context, user_id, user_text)


async def send_payment_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Генерирует ссылку Robokassa и отправляет пользователю.
    Требует переменные окружения ROBOKASSA_MERCHANT_LOGIN/ROBOKASSA_PASSWORD1/ROBOKASSA_PASSWORD2.
    """
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    product_code = context.user_data.get("selected_product")
    if not product_code or product_code not in PRODUCTS:
        await query.edit_message_text("Сначала выбери продукт, потом нажми «Оплатить».")
        return
    # Для групповых занятий подставляем тариф (VIP или Стандарт).
    if product_code == "group":
        product_code = "group_vip" if context.user_data.get("group_tariff") == "vip" else "group_standard"
    if product_code not in PRODUCTS:
        await query.edit_message_text("Сначала выбери тариф (VIP или Стандарт) для групповых занятий.")
        return

    try:
        cfg = RobokassaConfig.from_env()
        db = PaymentsDB.from_env()
    except Exception as e:
        logging.exception("Robokassa config/db error: %s", e)
        await query.edit_message_text("Оплата временно недоступна. Попробуй позже.")
        return

    product = PRODUCTS[product_code]
    amount = str(product["amount"])
    description = str(product["description"])

    inv_id, token = db.create_order(
        user_id=int(user.id),
        chat_id=int(chat.id),
        product_code=str(product_code),
        amount=amount,
        description=description,
    )

    shp = {
        "Shp_user_id": str(user.id),
        "Shp_chat_id": str(chat.id),
        "Shp_product": str(product_code),
        "Shp_order_token": token,
    }

    pay_url = build_payment_url(
        cfg=cfg,
        inv_id=inv_id,
        out_sum=amount,
        description=description,
        shp=shp,
    )

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Перейти к оплате", url=pay_url)]])
    await query.edit_message_text(
        "Ссылка для оплаты — под кнопкой ниже. После оплаты будет направлена вся необходимая информация.",
        reply_markup=kb,
        disable_web_page_preview=True,
    )

async def _reply_to_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    user_text: str,
) -> None:
    """Общая логика: добавить в историю, вызвать DeepSeek, отправить ответ (с потоком или без)."""
    add_to_history(user_id, "user", user_text)
    messages = get_history_messages(user_id)
    target = _get_reply_target(update)
    chat = update.effective_chat
    if not target or not chat:
        return

    await chat.send_action("typing")

    try:
        if STREAM_RESPONSE:
            stream = await client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                max_tokens=4800,
                temperature=1.75,
                stream=True,
            )
            accumulated = ""
            sent_msg = await target.reply_text("…")
            last_edit = 0.0
            edit_interval = 0.4

            async for chunk in stream:
                if not chunk.choices or not chunk.choices[0].delta.content:
                    continue
                accumulated += chunk.choices[0].delta.content
                now = asyncio.get_event_loop().time()
                if now - last_edit >= edit_interval or len(accumulated) < 50:
                    last_edit = now
                    try:
                        text = truncate_response(accumulated.strip()) or "…"
                        if len(text) > 4096:
                            text = text[:4093] + "..."
                        await sent_msg.edit_text(text)
                    except Exception:
                        pass

            reply_raw = truncate_response(accumulated.strip())
            if not reply_raw:
                reply_raw = "Не удалось сформировать ответ."
            reply_clean, step_id = _parse_step_from_reply(reply_raw)
            keyboard = _keyboard_for_step(step_id) if step_id else None
            if keyboard is None:
                reply_clean, keyboard = _parse_custom_buttons(reply_clean)
            final_text = reply_clean[:4096] if len(reply_clean) > 4096 else reply_clean
            final_text, parse_mode = _format_reply_for_telegram(final_text)
            if len(final_text) > 4096:
                final_text = final_text[:4093] + "..."
            try:
                await sent_msg.edit_text(
                    final_text,
                    parse_mode=parse_mode if parse_mode else None,
                    reply_markup=keyboard,
                )
            except Exception:
                pass
            add_to_history(user_id, "assistant", reply_clean or "")
        else:
            response = await client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                max_tokens=800,
                temperature=0.7,
                stream=False,
            )
            reply_raw = response.choices[0].message.content or ""
            reply_raw = truncate_response(reply_raw.strip())
            reply_clean, step_id = _parse_step_from_reply(reply_raw)
            keyboard = _keyboard_for_step(step_id) if step_id else None
            if keyboard is None:
                reply_clean, keyboard = _parse_custom_buttons(reply_clean)
            final_text = reply_clean[:4096] if len(reply_clean) > 4096 else reply_clean
            final_text, parse_mode = _format_reply_for_telegram(final_text)
            if len(final_text) > 4096:
                final_text = final_text[:4093] + "..."
            await target.reply_text(
                final_text,
                parse_mode=parse_mode if parse_mode else None,
                reply_markup=keyboard,
            )
            add_to_history(user_id, "assistant", reply_clean or "")
    except APIStatusError as e:
        if user_history[user_id]:
            user_history[user_id].pop()
        if e.status_code == 402:
            logging.warning("DeepSeek API: 402 Payment Required (Insufficient Balance). %s", e)
            await target.reply_text(
                "Сейчас сервис ответов временно недоступен (исчерпан баланс API). "
                "Попробуй позже или обратись к администратору бота."
            )
        else:
            logging.exception("DeepSeek API error: %s", e)
            await target.reply_text("Что-то пошло не так при ответе. Попробуй ещё раз или позже.")
    except Exception as e:
        logging.exception("DeepSeek API error: %s", e)
        if user_history[user_id]:
            user_history[user_id].pop()
        await target.reply_text(
            "Что-то пошло не так при ответе. Попробуй ещё раз или позже."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    text = update.message.text or ""
    if not text.strip():
        await update.message.reply_text("Напиши текстом, пожалуйста.")
        return
    await _reply_to_user(update, context, user_id, text.strip())


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update):
        return
    if not VOICE_ENABLED or not openai_client:
        await update.message.reply_text(
            "Голосовые сообщения пока не настроены. Напиши текстом."
        )
        return

    user_id = update.effective_user.id
    voice = update.message.voice
    await update.message.chat.send_action("typing")

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        try:
            file = await context.bot.get_file(voice.file_id)
            await file.download_to_drive(tmp.name)
        except Exception as e:
            logging.exception("Voice download error: %s", e)
            await update.message.reply_text("Не удалось загрузить голосовое сообщение.")
            return

    try:
        with open(tmp.name, "rb") as audio_file:
            transcript = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )
        user_text = (transcript.text or "").strip()
    except Exception as e:
        logging.exception("Whisper transcription error: %s", e)
        await update.message.reply_text("Не удалось распознать голос. Попробуй написать текстом.")
        return
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if not user_text:
        await update.message.reply_text("Текст не распознан. Попробуй ещё раз или напиши.")
        return

    await update.message.reply_text(f"🎤 Ты сказал(а): {user_text}")
    await _reply_to_user(update, context, user_id, user_text)


def build_application() -> Application:
    """Собирает и возвращает приложение бота (для polling или webhook)."""
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    if MAX_HISTORY_MESSAGES:
        app.add_handler(CommandHandler("new", cmd_new))
    if SUPPORT_TEXT:
        app.add_handler(CommandHandler("support", cmd_support))
    if PRIVACY_TEXT:
        app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CallbackQueryHandler(button_new_dialog, pattern="^new_dialog$"))
    app.add_handler(CallbackQueryHandler(button_start_chat, pattern="^start_chat$"))
    app.add_handler(CallbackQueryHandler(handle_step_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    if VOICE_ENABLED:
        app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    return app


async def process_webhook_update(update_body: str) -> None:
    """
    Обрабатывает один update от Telegram (режим webhook).
    Для использования в Cloud Functions: передайте сюда тело HTTP-запроса (JSON).
    """
    import json
    app = build_application()
    update_data = json.loads(update_body)
    update = Update.de_json(update_data, app.bot)
    await app.initialize()
    try:
        await app.process_update(update)
    finally:
        await app.shutdown()


def main() -> None:
    if LOG_TO_FILE:
        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            level=logging.INFO,
            filename="bot.log",
            encoding="utf-8",
        )
    else:
        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            level=logging.INFO,
        )

    app = build_application()
    print("Бот запущен. Остановка: Ctrl+C")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
