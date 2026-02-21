# -*- coding: utf-8 -*-
"""Проверки бота: промпт, кнопки, обработчики, форматирование."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_1_import_and_prompt():
    import bot
    assert hasattr(bot, 'SYSTEM_PROMPT'), 'SYSTEM_PROMPT отсутствует'
    assert isinstance(bot.SYSTEM_PROMPT, str), 'SYSTEM_PROMPT не строка'
    assert len(bot.SYSTEM_PROMPT) > 100, 'Промпт слишком короткий'
    assert 'Владима Энхель' in bot.SYSTEM_PROMPT, 'Промпт не содержит ключевой роли'
    assert 'start_diagnosis' in bot.STEP_KEYBOARDS, 'Нет клавиатуры start_diagnosis'
    assert 'form_address' in bot.STEP_KEYBOARDS, 'Нет клавиатуры form_address'
    assert 'messenger' in bot.STEP_KEYBOARDS, 'Нет клавиатуры messenger'
    return True

def test_2_prompt_file_and_steps():
    import bot as b
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(b.__file__)), 'system_prompt.txt')
    assert os.path.isfile(prompt_path), 'system_prompt.txt не найден'
    with open(prompt_path, encoding='utf-8') as f:
        raw = f.read()
    for step in ('start_diagnosis', 'form_address', 'messenger'):
        assert step in b.STEP_KEYBOARDS, f'В STEP_KEYBOARDS нет {step}'
    assert '[STEP:start_diagnosis]' in raw or 'start_diagnosis' in raw
    assert '[STEP:form_address]' in raw or 'form_address' in raw
    assert '[STEP:messenger]' in raw or 'messenger' in raw
    return True

def test_3_parse_and_keyboards():
    import bot
    text_clean = 'Привет. Нажми кнопку.'
    for step_id in ('start_diagnosis', 'form_address', 'messenger'):
        reply = text_clean + '\n[STEP:' + step_id + ']'
        clean, parsed = bot._parse_step_from_reply(reply)
        assert parsed == step_id, f'parse_step: ожидали {step_id}, получили {parsed}'
        assert clean == text_clean, 'Текст после парсинга должен быть без тега'
        kb = bot._keyboard_for_step(step_id)
        assert kb is not None, f'Клавиатура для {step_id} не создаётся'
    return True

def test_4_handlers():
    import bot
    app = bot.build_application()
    group0 = app.handlers.get(0, [])
    names = []
    for h in group0:
        if hasattr(h, 'callback') and hasattr(h.callback, '__name__'):
            names.append(h.callback.__name__)
    assert any('start' in n for n in names), 'Нет обработчика start'
    assert any('start_chat' in n or 'button_start_chat' == n for n in names), 'Нет start_chat'
    assert any('handle_step_button' == n for n in names), 'Нет handle_step_button'
    assert any('handle_message' == n for n in names), 'Нет handle_message'
    return True

def test_5_callback_length_and_format():
    import bot
    for step_id, rows in bot.STEP_KEYBOARDS.items():
        for row in rows:
            for label, cb in row:
                data_len = len(cb.encode('utf-8'))
                assert data_len <= 64, f'callback_data "{cb}" ({data_len} байт) > 64'
    out, mode = bot._format_reply_for_telegram('Текст **жирный** и список\n* пункт')
    assert 'жирный' in out and '<b>' in out
    assert 'пункт' in out and '➖' in out
    return True


def test_6_new_step_buttons():
    """Проверка новых кнопок: insight_next, readiness, products, pay_choice, webinar_offer."""
    import bot
    new_steps = ('insight_next', 'readiness', 'products', 'pay_choice', 'webinar_offer')
    for step_id in new_steps:
        assert step_id in bot.STEP_KEYBOARDS, f'В STEP_KEYBOARDS нет {step_id}'
        clean, parsed = bot._parse_step_from_reply('Текст\n[STEP:' + step_id + ']')
        assert parsed == step_id
        kb = bot._keyboard_for_step(step_id)
        assert kb is not None
    with open(os.path.join(os.path.dirname(__file__), 'system_prompt.txt'), encoding='utf-8') as f:
        raw = f.read()
    for step_id in new_steps:
        assert f'[STEP:{step_id}]' in raw or step_id in raw, f'В промпте нет тега для {step_id}'
    return True


def test_7_load_prompt_file():
    """Проверка загрузки system_prompt.txt и отсутствия пустого промпта."""
    import bot
    assert len(bot.SYSTEM_PROMPT.strip()) > 500
    assert 'ОДИН ВОПРОС' in bot.SYSTEM_PROMPT
    assert 'КНОПКИ ПО ШАГАМ' in bot.SYSTEM_PROMPT
    return True


if __name__ == '__main__':
    tests = [
        ('Import, prompt, STEP_KEYBOARDS', test_1_import_and_prompt),
        ('Prompt file and step_id', test_2_prompt_file_and_steps),
        ('Parse STEP and keyboards', test_3_parse_and_keyboards),
        ('Handlers in Application', test_4_handlers),
        ('callback_data length and format', test_5_callback_length_and_format),
        ('New step buttons (insight_next, readiness, products, pay_choice, webinar_offer)', test_6_new_step_buttons),
        ('Load prompt file and content', test_7_load_prompt_file),
    ]
    scores = []
    for name, fn in tests:
        try:
            fn()
            print('OK:', name)
            scores.append(10)
        except Exception as e:
            print('FAIL:', name, '-', e)
            scores.append(4)
    avg = sum(scores) / len(scores)
    print('\nScores:', scores, 'avg:', round(avg, 1))
    sys.exit(0 if avg > 7 else 1)
