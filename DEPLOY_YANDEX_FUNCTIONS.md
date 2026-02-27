# Деплой бота в Yandex Cloud Functions (webhook)

Бот переводится в режим **webhook**: Telegram при каждом сообщении отправляет POST-запрос на URL вашей функции. Функция вызывается, обрабатывает update и отвечает. Платите только за вызовы и время выполнения.

---

## Ограничения по сравнению с ВМ

| Что учесть | Описание |
|------------|----------|
| **История диалога** | В текущей реализации история хранится в памяти. В Cloud Functions каждый вызов может выполняться в новом экземпляре, поэтому **история между сообщениями не сохраняется** (каждое сообщение для бота будет «началом разговора»). Чтобы сохранять историю, нужна внешняя БД (YDB, Redis и т.п.) и доработка кода. |
| **Таймаут** | Обычно до 60 секунд на один вызов. Ответ DeepSeek должен уложиться в это время (потоковый вывод в функции возможен, но итоговое время обработки всё равно ограничено). |
| **Холодный старт** | Первый запрос после простоя может быть медленнее (запуск контейнера). |

Если для вас важна непрерывная история диалога без доработок — используйте [развёртывание на ВМ](DEPLOY_YANDEX_CLOUD.md).

---

## Что уже сделано в коде

- В **bot.py** добавлены:
  - `build_application()` — сборка приложения;
  - `process_webhook_update(update_body)` — обработка одного update (тело POST от Telegram).
- В **deploy/handler_webhook.py** — обработчик для Yandex Cloud Functions: читает `event["body"]`, вызывает `process_webhook_update`, возвращает 200.

Дальше нужно: создать функцию в Yandex Cloud, загрузить код, выставить переменные окружения и указать Telegram webhook на URL функции.

---

## Шаг 1. Регистрация и биллинг

Как в [DEPLOY_YANDEX_CLOUD.md](DEPLOY_YANDEX_CLOUD.md): зайдите в [console.cloud.yandex.ru](https://console.cloud.yandex.ru), создайте платёжный аккаунт и каталог при необходимости.

---

## Шаг 2. Создание функции

1. В консоли: **Serverless** → **Cloud Functions** (или «Функции»).
2. **Создать функцию**.
3. Имя, например: `telegram-ai-psychologist`.
4. Среда: **Python 3.12** (или актуальная из списка).
5. Создайте функцию без триггера — триггер добавим отдельно.

---

## Шаг 3. Загрузка кода

Функция должна содержать весь код бота (чтобы работали импорты из `bot`).

### Вариант A: ZIP-архив

1. На компьютере в папке с ботом положите в архив:
   - `bot.py`
   - `requirements.txt`
   - папку `deploy/` с `handler_webhook.py`
2. В консоли функции: **Редактировать** → **Код** → загрузить ZIP.
3. **Точка входа**: `deploy.handler_webhook.handler` (модуль.файл.имя_функции).
4. **Таймаут**: не менее 60 секунд (в настройках функции).
5. **Память**: 512 МБ или больше.

### Вариант B: через CLI (YC CLI)

Установите [YC CLI](https://cloud.yandex.ru/docs/cli/quickstart), выполните из папки проекта:

```bash
zip -r function.zip bot.py requirements.txt deploy/
yc serverless function version create --function-name=telegram-ai-psychologist --runtime=python312 --entrypoint=deploy.handler_webhook.handler --memory=512m --execution-timeout=60s --source-path=function.zip
```

---

## Шаг 4. Переменные окружения

В настройках функции задайте переменные (секреты не показывать в логах):

- `TELEGRAM_BOT_TOKEN` — токен от BotFather.
- `DEEPSEEK_API_KEY` — ключ DeepSeek.
- `OPENAI_API_KEY` — ключ OpenAI (если нужны голосовые).

В консоли: **Редактировать** → **Переменные окружения** → добавить пары ключ–значение.

---

## Шаг 5. HTTP-триггер и URL

1. В разделе функции откройте **Триггеры** → **Создать триггер**.
2. Тип: **HTTP** (или «Триггер для приложения в Serverless Containers» — зависит от интерфейса; нужен именно вызов по URL).
3. Укажите функцию и версию.
4. После создания триггера появится **URL вызова**, например:
   `https://functions.yandexcloud.net/d4e.../telegram-ai-psychologist`
5. Этот URL понадобится для webhook Telegram.

Если в вашем регионе интерфейс отличается, в документации Yandex Cloud найдите: «Функция с HTTP-триггером» и создайте триггер по инструкции.

---

## Шаг 6. Установка webhook в Telegram

Telegram должен слать обновления на URL вашей функции. Вызовите метод API один раз (с компьютера или с любого сервера):

```text
https://api.telegram.org/bot<ВАШ_TELEGRAM_BOT_TOKEN>/setWebhook?url=<URL_ВАШЕЙ_ФУНКЦИИ>
```

Пример:

```text
https://api.telegram.org/bot123456:ABC-DEF/setWebhook?url=https://functions.yandexcloud.net/d4e.../telegram-ai-psychologist
```

Откройте эту ссылку в браузере (или выполните запрос через curl). В ответе должно быть `"ok": true`.

Проверка текущего webhook:

```text
https://api.telegram.org/bot<ТОКЕН>/getWebhookInfo
```

Чтобы снова перейти на polling (например, запуск на ВМ), удалите webhook:

```text
https://api.telegram.org/bot<ТОКЕН>/deleteWebhook
```

---

## Шаг 7. Проверка

Напишите боту в Telegram. Если функция настроена правильно и переменные заданы, бот ответит. В логах функции (в консоли Yandex Cloud: **Логи** выбранной функции) будут видны ошибки, если что-то пойдёт не так.

---

## Обновление кода функции

После изменений в `bot.py` или `handler_webhook.py`:

1. Соберите новый ZIP с актуальными файлами (включая `bot.py`, `requirements.txt`, `deploy/`).
2. В консоли создайте **новую версию** функции с этим архивом (или загрузите код заново).
3. Убедитесь, что HTTP-триггер привязан к новой версии (если нужно выбрать версию вручную).

Webhook в Telegram менять не нужно — URL остаётся тем же.

**Примечание:** при деплое на **виртуальную машину** (а не в Cloud Functions) для обновления с ПК на ВМ можно использовать скрипты автообновления: `deploy_to_vm.bat`, `deploy_local.ps1`, `deploy_remote.ps1`. Подробности — в [DEPLOY_YANDEX_CLOUD.md](DEPLOY_YANDEX_CLOUD.md) (Часть 8).

---

## Итог

- **Достаточно ли «просто функции» вместо ВМ?** Да, если вас устраивает отсутствие истории диалога между сообщениями и ограничение по таймауту. Тогда деплой в Cloud Functions проще и часто дешевле при малом трафике.
- Для полноценного диалога с памятью без доработок используйте [ВМ](DEPLOY_YANDEX_CLOUD.md); при желании потом можно добавить БД и в вариант с функциями.

---

## Robokassa (оплата): отдельный HTTP-обработчик

Если вы включили оплату через Robokassa, вам нужен **ещё один HTTP URL** (кроме Telegram webhook), потому что Robokassa будет слать запросы на ваши `ResultURL/SuccessURL/FailURL`.

### Что есть в коде

- `deploy/handler_robokassa.py`
  - `handler_result` — **ResultURL** (server-to-server, подтверждает оплату). Возвращает `OK{InvId}`.
  - `handler_success` — **SuccessURL** (редирект пользователя после оплаты, не подтверждает оплату).
  - `handler_fail` — **FailURL**.

### Как задеплоить

1. Создайте **вторую Cloud Function** (или вторую версию функции с другим entrypoint) и загрузите тот же ZIP (как в шаге 3).
2. Для обработчика ResultURL укажите:
   - **Точка входа**: `deploy.handler_robokassa.handler_result`
3. (Опционально) для SuccessURL/FailURL создайте ещё функции/версии с entrypoint:
   - `deploy.handler_robokassa.handler_success`
   - `deploy.handler_robokassa.handler_fail`
4. В переменных окружения добавьте Robokassa-настройки (см. `.env.example`):
   - `ROBOKASSA_MERCHANT_LOGIN`, `ROBOKASSA_PASSWORD1`, `ROBOKASSA_PASSWORD2`
   - ссылки доступа: `WEBINAR_ACCESS_URL`, `GROUP_COURSE_ACCESS_URL`, `PRO_BOT_URL`

### Что настроить в личном кабинете Robokassa

- **Result URL**: URL функции с `handler_result`
- **Success URL**: URL функции с `handler_success` (опционально)
- **Fail URL**: URL функции с `handler_fail` (опционально)
