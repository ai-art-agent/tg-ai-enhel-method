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
import json
import logging
import tempfile
import time
import asyncio
from collections import defaultdict
from typing import Optional, Callable

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
_VALIDATOR_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validator_prompt.txt")
_SIMULATOR_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_simulator_prompt.txt")

load_dotenv()
# Максимальный размер ответа в символах (для промптов). В system_prompt.txt и validator_prompt.txt
# используйте плейсхолдер {{MAX_RESPONSE_CHARS}} — он подставится при загрузке. Можно задать в .env.
try:
    MAX_RESPONSE_CHARS = int(os.getenv("MAX_RESPONSE_CHARS", "350"))
except (TypeError, ValueError):
    MAX_RESPONSE_CHARS = 350
PLACEHOLDER_MAX_RESPONSE = "{{MAX_RESPONSE_CHARS}}"


def _load_system_prompt() -> str:
    """Загружает системный промпт из файла system_prompt.txt. Подставляет {{MAX_RESPONSE_CHARS}}."""
    try:
        with open(_PROMPT_PATH, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            raise ValueError("Файл system_prompt.txt пуст.")
        content = content.replace(PLACEHOLDER_MAX_RESPONSE, str(MAX_RESPONSE_CHARS))
        return content
    except FileNotFoundError:
        raise ValueError(
            f"Не найден файл с промптом: {_PROMPT_PATH}. "
            "Положите system_prompt.txt в папку с bot.py."
        )
    except OSError as e:
        raise ValueError(f"Не удалось прочитать system_prompt.txt: {e}") from e


SYSTEM_PROMPT = _load_system_prompt()


def _load_validator_prompt() -> str:
    """Загружает промпт валидатора из validator_prompt.txt. Подставляет {{MAX_RESPONSE_CHARS}}."""
    try:
        with open(_VALIDATOR_PROMPT_PATH, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return ""
        content = content.replace(PLACEHOLDER_MAX_RESPONSE, str(MAX_RESPONSE_CHARS))
        return content
    except FileNotFoundError:
        logging.warning("Файл validator_prompt.txt не найден, валидация отключена.")
        return ""
    except OSError as e:
        logging.warning("Не удалось прочитать validator_prompt.txt: %s", e)
        return ""


VALIDATOR_PROMPT = _load_validator_prompt()
VALIDATOR_ENABLED = bool(VALIDATOR_PROMPT)
# 0 = одна проверка, без перегенерации. 1 = одна перегенерация при отклонении (три облака: ответ → вердикт → новый ответ).
MAX_VALIDATION_RETRIES = 1


def _load_simulator_prompt() -> str:
    """Загружает промпт симулятора пользователя из user_simulator_prompt.txt (для автодиалога «два бота»)."""
    try:
        with open(_SIMULATOR_PROMPT_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        logging.warning("Файл user_simulator_prompt.txt не найден, автодиалог недоступен.")
        return ""
    except OSError as e:
        logging.warning("Не удалось прочитать user_simulator_prompt.txt: %s", e)
        return ""


SIMULATOR_PROMPT = _load_simulator_prompt()
SIMULATOR_ENABLED = bool(SIMULATOR_PROMPT)

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
    "readiness": None,  # строится в _keyboard_for_step по context.user_data["form_address"]
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

# Парсинг тега [STEP:step_id] в ответе модели. Ищем последнее вхождение, чтобы кнопки показывались
# даже если модель добавила текст после тега или пробел после двоеточия.
STEP_TAG_REGEX = re.compile(r"\[STEP:\s*(\w+)\]", re.IGNORECASE)
# Удаляем любой [STEP:xxx] из текста перед показом пользователю (тег служебный).
STEP_TAG_ANYWHERE = re.compile(r"\s*\[STEP:\s*\w+\]\s*", re.IGNORECASE)
# Автогенерация кнопок: [BUTTONS: Текст1 | Текст2 | Текст3] (до 4 кнопок, до 64 байт на callback_data).
BUTTONS_TAG_REGEX = re.compile(r"\s*\[BUTTONS:\s*([^\]]+)\]", re.IGNORECASE)
CALLBACK_DATA_MAX_BYTES = 64

# Маркер списка вместо "*" / "-" (модель часто выводит Markdown, в Telegram без parse_mode они видны как символы).
LIST_MARKER = "➖"

# ============== КОД БОТА ==============

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


def _strip_step_tags_for_display(text: str) -> str:
    """Удаляет все [STEP:xxx] из текста, чтобы служебный тег не показывался пользователю."""
    if not text or not text.strip():
        return text
    out = STEP_TAG_ANYWHERE.sub(" ", text)
    return re.sub(r"\s+", " ", out).strip() or "…"


def _parse_step_from_reply(reply: str) -> tuple[str, Optional[str]]:
    """Ищет последнее вхождение [STEP:step_id] в ответе, убирает его и всё после него; возвращает (очищенный текст, step_id или None)."""
    matches = list(STEP_TAG_REGEX.finditer(reply))
    if not matches:
        return reply, None
    last = matches[-1]
    step_id = last.group(1).lower()
    # Показываем пользователю только текст до тега (тег и всё после — скрыты).
    reply_clean = reply[: last.start()].rstrip()
    # Для [STEP:custom] после тега идёт [BUTTONS: ...] — оставляем хвост для _parse_custom_buttons.
    if step_id == "custom":
        reply_clean = (reply_clean + " " + reply[last.end() :].lstrip()).strip()
    # Убираем любой оставшийся [STEP:xxx] из текста (модель могла вставить тег в начало или середину).
    reply_clean = STEP_TAG_ANYWHERE.sub(" ", reply_clean)
    reply_clean = re.sub(r"\s+", " ", reply_clean).strip()
    return reply_clean, step_id


def _readiness_label_and_callback(form_address: Optional[str]) -> tuple[str, str]:
    """Подпись и callback кнопки для шага readiness (обезличенно)."""
    return "Хочу продолжить", "Хочу продолжить"


def _keyboard_for_step(step_id: str, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> Optional[InlineKeyboardMarkup]:
    """Клавиатура по step_id; для readiness подпись кнопки зависит от context.user_data['form_address']; для pay_choice в callback «Оплатить» зашивается код продукта."""
    if step_id == "readiness":
        label, callback = _readiness_label_and_callback(
            context.user_data.get("form_address") if context else None
        )
        rows = [[(label, callback), ("Еще подумаю", "Еще подумаю")]]
        return InlineKeyboardMarkup([[InlineKeyboardButton(str(btn_label), callback_data=str(btn_cb)) for btn_label, btn_cb in row] for row in rows])

    if step_id == "pay_choice" and context:
        product_code = context.user_data.get("selected_product")
        if product_code == "group":
            product_code = "group_vip" if context.user_data.get("group_tariff") == "vip" else "group_standard"
        if product_code and product_code in PRODUCTS:
            rows = [[("Оплатить", f"pay:{product_code}")], [("Еще думаю", "Еще думаю")]]
            return InlineKeyboardMarkup([[InlineKeyboardButton(str(l), callback_data=str(c)) for l, c in row] for row in rows])

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


async def _validate_reply(reply_raw: str) -> tuple[bool, list[str], list[str], str]:
    """
    Проверяет ответ основной модели через промпт-валидатор.
    Возвращает (valid, errors, recommendations, raw_response). При сбое валидации считаем ответ валидным.
    """
    if not VALIDATOR_ENABLED or not reply_raw or not reply_raw.strip():
        return True, [], [], ""
    try:
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": VALIDATOR_PROMPT},
                {"role": "user", "content": f"ANSWER:\n{reply_raw}"},
            ],
            max_tokens=500,
            temperature=0,
        )
        raw_text = (response.choices[0].message.content or "").strip()
        text = raw_text
        # Убрать обёртку ```json ... ```
        if "```" in text:
            for part in re.split(r"```\w*", text):
                part = part.strip()
                if part.startswith("{"):
                    text = part
                    break
        data = json.loads(text)
        valid = data.get("valid", True)
        errors = data.get("errors") or []
        if isinstance(errors, list):
            errors = [str(e) for e in errors]
        else:
            errors = [str(errors)] if errors else []
        recommendations = data.get("recommendations") or []
        if isinstance(recommendations, list):
            recommendations = [str(r) for r in recommendations]
        else:
            recommendations = [str(recommendations)] if recommendations else []
        return bool(valid), errors, recommendations, raw_text
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logging.warning("Валидатор вернул невалидный JSON: %s", e)
        return True, [], [], ""
    except Exception as e:
        logging.warning("Ошибка валидатора: %s", e)
        return True, [], [], ""


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


def _apply_product_and_tariff_from_text(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """
    По тексту пользователя (например «ВИП», «Групповые занятия») выставляет
    context.user_data["selected_product"] и при необходимости ["group_tariff"],
    чтобы кнопка «Оплатить» сработала и при ответе текстом, а не только по кнопке.
    """
    if not text:
        return
    t = text.strip()
    # Точное совпадение с кнопками продуктов
    if t in PRODUCT_BUTTON_TO_CODE:
        context.user_data["selected_product"] = PRODUCT_BUTTON_TO_CODE[t]
        return
    # ВИП / VIP — тариф групповых
    if t.upper() in ("ВИП", "VIP"):
        context.user_data["group_tariff"] = "vip"
        if context.user_data.get("selected_product") is None:
            context.user_data["selected_product"] = "group"
        return
    # Стандарт — тариф групповых
    if t.lower() == "стандарт":
        context.user_data["group_tariff"] = "standard"
        if context.user_data.get("selected_product") is None:
            context.user_data["selected_product"] = "group"


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

    # Запоминаем форму обращения для обращения к пользователю; кнопка readiness — «Хочу продолжить».
    if user_text in ("Мужская форма обращения", "Женская форма обращения", "Нейтральная форма обращения"):
        context.user_data["form_address"] = user_text

    # Запоминаем выбранный продукт, чтобы "Оплатить" мог выдать правильную ссылку.
    if user_text in PRODUCT_BUTTON_TO_CODE:
        context.user_data["selected_product"] = PRODUCT_BUTTON_TO_CODE[user_text]

    # При выборе групповых занятий запоминаем тариф (VIP / Стандарт).
    # Если продукт ещё не был выбран кнопкой (например, написали текстом), считаем, что это групповые — иначе кнопки VIP/Стандарт не показываются.
    if user_text == "VIP":
        context.user_data["group_tariff"] = "vip"
        if context.user_data.get("selected_product") is None:
            context.user_data["selected_product"] = "group"
    elif user_text == "Стандарт":
        context.user_data["group_tariff"] = "standard"
        if context.user_data.get("selected_product") is None:
            context.user_data["selected_product"] = "group"

    # Специальная обработка оплаты (не отправляем это в модель).
    if user_text.lower() == "оплатить":
        await send_payment_link(update, context)
        return
    if user_text.startswith("pay:") and len(user_text) > 4:
        product_code = user_text[4:].strip()
        if product_code in PRODUCTS:
            await send_payment_link(update, context, product_code_override=product_code)
            return

    await _reply_to_user(update, context, user_id, user_text)


async def send_payment_link(update: Update, context: ContextTypes.DEFAULT_TYPE, product_code_override: Optional[str] = None) -> None:
    """
    Генерирует ссылку Robokassa и отправляет пользователю.
    product_code_override: если задан, используется вместо context.user_data (кнопка «Оплатить» с callback pay:КОД).
    """
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    product_code = product_code_override or context.user_data.get("selected_product")
    if not product_code or product_code not in PRODUCTS:
        await query.edit_message_text("Сначала выбери продукт, потом нажми «Оплатить».")
        return
    if not product_code_override and product_code == "group":
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


async def _generate_reply(msgs: list[dict], stream: bool = False, on_chunk: Optional[Callable[[str], None]] = None) -> str:
    """Генерация ответа модели. При stream=True и on_chunk вызывается on_chunk(accumulated) для каждого фрагмента (on_chunk может быть async)."""
    if stream:
        stream_obj = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=msgs,
            max_tokens=4800,
            temperature=1.75,
            stream=True,
        )
        accumulated = ""
        async for chunk in stream_obj:
            if chunk.choices and chunk.choices[0].delta.content:
                accumulated += chunk.choices[0].delta.content
                if on_chunk:
                    try:
                        result = on_chunk(accumulated)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        pass
        return truncate_response(accumulated.strip()) or "Не удалось сформировать ответ."
    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=msgs,
        max_tokens=4800,
        temperature=1.75,
        stream=False,
    )
    raw = response.choices[0].message.content or ""
    return truncate_response(raw.strip()) or "Не удалось сформировать ответ."


async def get_bot_reply(
    user_id: int,
    user_text: str,
    context: Optional[ContextTypes.DEFAULT_TYPE] = None,
    log_validator_full: bool = False,
    validator_callback: Optional[Callable[[str], None]] = None,
    stream_callback: Optional[Callable[[str], None]] = None,
):
    """
    Один шаг диалога без Telegram. Возвращает (reply_clean, buttons, validator_outputs, timings, rejected_reply_clean).
    validator_outputs: список (raw_validator, reply_raw, validator_ms).
    timings: {"psychologist_ms": int}.
    rejected_reply_clean: если была перегенерация — текст первого (отклонённого) ответа для отображения первым облаком; иначе None.
    stream_callback(text_so_far): при задании ответ психолога стримится по фрагментам.
    """
    add_to_history(user_id, "user", user_text)
    messages = get_history_messages(user_id)
    use_stream = stream_callback is not None
    t0_psych = time.monotonic()
    reply_raw = await _generate_reply(
        messages, stream=use_stream, on_chunk=stream_callback if use_stream else None
    )
    psychologist_ms = int((time.monotonic() - t0_psych) * 1000)

    validator_outputs = []
    rejected_reply_clean = None
    retries = 0
    while VALIDATOR_ENABLED and retries <= MAX_VALIDATION_RETRIES:
        # Валидатор вызываем только для первого ответа; после перегенерации второй ответ не проверяем (лимит перегенераций всё равно исчерпан).
        if retries == 0:
            t0_val = time.monotonic()
            valid, errors, recommendations, raw_validator = await _validate_reply(reply_raw)
            validator_ms = int((time.monotonic() - t0_val) * 1000)
            if log_validator_full and raw_validator:
                logging.info("Валидатор (полный ответ): %s", raw_validator)
            if raw_validator:
                validator_outputs.append((raw_validator, reply_raw, validator_ms))
            if validator_callback and raw_validator:
                try:
                    validator_callback(raw_validator)
                except Exception:
                    pass
            if valid:
                break
        else:
            valid = True
            break
        if retries >= MAX_VALIDATION_RETRIES:
            logging.info(
                "Валидатор: исчерпан лимит перегенераций (%d), оставляем последний ответ. Ошибки: %s",
                MAX_VALIDATION_RETRIES,
                "; ".join(errors) if errors else "—",
            )
            break
        # Сохраняем отклонённый ответ для отображения первым облаком (ответ → вердикт → новый ответ).
        rej_clean, rej_step = _parse_step_from_reply(reply_raw)
        rej_kb = _keyboard_for_step(rej_step, context) if rej_step else None
        if rej_kb is None:
            rej_clean, _ = _parse_custom_buttons(rej_clean)
        rejected_reply_clean = (rej_clean or "").strip()
        logging.info(
            "Валидатор: ответ отклонён — %s. Перегенерация %d/%d.",
            "; ".join(errors) if errors else "—",
            retries + 1,
            MAX_VALIDATION_RETRIES,
        )
        retry_parts = ["Твой ответ отклонён."]
        if errors:
            retry_parts.append("Нарушения: " + "; ".join(errors) + ".")
        if recommendations:
            retry_parts.append("Рекомендации: " + "; ".join(recommendations) + ".")
        retry_parts.append("Ответь заново, исправив перечисленное. Важно: не извиняйся перед пользователем, не пиши «прошу прощения за ошибку», «извините» и т.п. — пользователь не видит валидатор. Просто дай исправленный ответ.")
        retry_messages = messages + [
            {"role": "assistant", "content": reply_raw},
            {"role": "user", "content": " ".join(retry_parts)},
        ]
        t0_retry = time.monotonic()
        reply_raw = await _generate_reply(retry_messages, stream=False)
        psychologist_ms += int((time.monotonic() - t0_retry) * 1000)
        retries += 1
        messages = retry_messages

    reply_clean, step_id = _parse_step_from_reply(reply_raw)
    keyboard = _keyboard_for_step(step_id, context) if step_id else None
    if keyboard is None:
        reply_clean, keyboard = _parse_custom_buttons(reply_clean)
    add_to_history(user_id, "assistant", reply_clean or "")
    buttons = []
    if keyboard and hasattr(keyboard, "inline_keyboard"):
        for row in keyboard.inline_keyboard:
            for btn in row:
                buttons.append((getattr(btn, "text", ""), getattr(btn, "callback_data", "")))
    timings = {"psychologist_ms": psychologist_ms}
    return (reply_clean or "").strip(), buttons, validator_outputs, timings, rejected_reply_clean


def _is_terminal_action(simulator_message: str) -> bool:
    """Проверяет, что симулятор выбрал оплату или отказ — диалог можно завершать и запрашивать SHOW_JSON."""
    s = (simulator_message or "").strip()
    return s.startswith("pay:") or s in ("Еще думаю", "Оплатить")


async def get_simulator_reply(user_id: int, buttons: list[tuple]) -> str:
    """
    Один ответ «пользователя» от второго бота (симулятор). Используется в автодиалоге «два бота».
    Возвращает одну строку: либо текст от имени пользователя, либо callback_data кнопки.
    """
    if not SIMULATOR_ENABLED:
        raise RuntimeError("Симулятор отключён: отсутствует user_simulator_prompt.txt")
    messages = get_history_messages(user_id)
    # Без system, только диалог
    parts = []
    for m in messages:
        if m.get("role") == "system":
            continue
        who = "Психолог" if m.get("role") == "assistant" else "Пользователь"
        parts.append(f"{who}: {m.get('content', '')}")
    conv = "\n\n".join(parts)
    if buttons:
        lines = [f"- {label} -> {cb}" for label, cb in buttons]
        conv += "\n\nТекущие кнопки (ответь ровно одним callback_data или своим текстом):\n" + "\n".join(lines)
    else:
        conv += "\n\nКнопок нет. Ответь текстом от имени пользователя."
    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SIMULATOR_PROMPT},
            {"role": "user", "content": conv},
        ],
        max_tokens=200,
        temperature=0.7,
    )
    raw = (response.choices[0].message.content or "").strip()
    # Одна строка: берём первую, обрезаем по переносу
    return raw.split("\n")[0].strip() if raw else ""


async def _reply_to_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    user_text: str,
) -> None:
    """Общая логика: добавить в историю, вызвать DeepSeek, при необходимости перегенерировать по валидатору, отправить ответ."""
    add_to_history(user_id, "user", user_text)
    messages = get_history_messages(user_id)
    target = _get_reply_target(update)
    chat = update.effective_chat
    if not target or not chat:
        return

    await chat.send_action("typing")

    try:
        sent_msg = await target.reply_text("…")

        # Потоковый вывод только для последней попытки (не подлежащей корректировке валидатором). Троттлинг ~0.2 с.
        last_stream_edit = [0.0]
        STREAM_THROTTLE_SEC = 0.4

        async def stream_edit(accumulated: str) -> None:
            display, _ = _parse_step_from_reply(accumulated)
            display = (display or "…").strip()
            if len(display) > 4090:
                display = display[:4090] + "..."
            now = time.monotonic()
            if now - last_stream_edit[0] >= STREAM_THROTTLE_SEC or not last_stream_edit[0]:
                try:
                    await sent_msg.edit_text(display or "…")
                    last_stream_edit[0] = now
                except Exception:
                    pass

        # Первая попытка — без стрима (будет валидироваться)
        reply_raw = await _generate_reply(messages, stream=False)

        # Валидация и при необходимости перегенерация
        retries = 0
        while VALIDATOR_ENABLED and retries <= MAX_VALIDATION_RETRIES:
            valid, errors, recommendations, _ = await _validate_reply(reply_raw)
            if valid:
                if retries > 0:
                    logging.info("Валидатор: ответ принят после перегенерации (попытка %d).", retries + 1)
                break
            if retries >= MAX_VALIDATION_RETRIES:
                logging.info(
                    "Валидатор: исчерпан лимит перегенераций (%d), оставляем последний ответ. Ошибки: %s",
                    MAX_VALIDATION_RETRIES,
                    "; ".join(errors) if errors else "—",
                )
                break
            logging.info(
                "Валидатор: ответ отклонён — %s. Перегенерация %d/%d.",
                "; ".join(errors) if errors else "—",
                retries + 1,
                MAX_VALIDATION_RETRIES,
            )
            retry_parts = ["Твой ответ отклонён."]
            if errors:
                retry_parts.append("Нарушения: " + "; ".join(errors) + ".")
            if recommendations:
                retry_parts.append("Рекомендации: " + "; ".join(recommendations) + ".")
            retry_parts.append("Ответь заново, исправив перечисленное.")
            retry_messages = messages + [
                {"role": "assistant", "content": reply_raw},
                {"role": "user", "content": " ".join(retry_parts)},
            ]
            # В последовательный (потоковый) вывод передаём только последнюю попытку — она не валидируется
            try:
                await sent_msg.edit_text("…")
            except Exception:
                pass
            last_stream_edit[0] = 0.0
            reply_raw = await _generate_reply(retry_messages, stream=True, on_chunk=stream_edit)
            retries += 1
            messages = retry_messages

        reply_clean, step_id = _parse_step_from_reply(reply_raw)
        keyboard = _keyboard_for_step(step_id, context) if step_id else None
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
    except APIStatusError as e:
        if user_history[user_id]:
            user_history[user_id].pop()
        err_text = (
            "Сейчас сервис ответов временно недоступен (исчерпан баланс API). Попробуй позже или обратись к администратору бота."
            if e.status_code == 402
            else "Что-то пошло не так при ответе. Попробуй ещё раз или позже."
        )
        try:
            await sent_msg.edit_text(err_text)
        except Exception:
            await target.reply_text(err_text)
    except Exception as e:
        logging.exception("DeepSeek API error: %s", e)
        if user_history[user_id]:
            user_history[user_id].pop()
        try:
            await sent_msg.edit_text("Что-то пошло не так при ответе. Попробуй ещё раз или позже.")
        except Exception:
            await target.reply_text("Что-то пошло не так при ответе. Попробуй ещё раз или позже.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update):
        return
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Напиши текстом, пожалуйста.")
        return

    # Сохраняем выбор продукта/тарифа и при текстовом ответе (напр. «ВИП», «Групповые занятия»),
    # чтобы кнопка «Оплатить» потом работала.
    _apply_product_and_tariff_from_text(context, text)

    await _reply_to_user(update, context, user_id, text)


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

    _apply_product_and_tariff_from_text(context, user_text)

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
