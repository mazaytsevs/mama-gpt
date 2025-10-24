# mama-gpt

Телеграм-бот, который отвечает на вопросы мамы и Маши при помощи GigaChat.

## Основные возможности
- Long polling из коробки (сервер не нужен), webhook можно включить для продового деплоя.
- Ограничение доступа по списку разрешённых пользователей + команды для администратора.
- История диалога в Redis с TTL 7 дней и системный prompt в двух режимах: friendly/concise.
- Клиент GigaChat с получением access token через Basic → Bearer и обработкой retry/timeout.
- Структурные JSON-логи, счётчики Prometheus (`/metrics`) и health-check (`/healthz`).

## Запуск локально (polling)
1. Скопируйте `.env.example` в `.env` и заполните обязательные переменные (`TELEGRAM_BOT_TOKEN`, списки ID, GigaChat настройки).
2. Установите зависимости: `pip install -r requirements.txt` (при необходимости `-r requirements-dev.txt`).
3. Запустите long polling (значение `APP_MODE` по умолчанию — `polling`):
   ```bash
   python -m app.main
   ```

## Запуск через Docker
```bash
cp .env.example .env
# заполните .env
docker compose up --build
```
Контейнер стартует в режиме polling. Если нужен webhook, задайте `APP_MODE=webhook` и заполните секреты.

## Webhook (опционально)
1. Задайте в `.env`: `APP_MODE=webhook`, `WEBHOOK_SECRET_PATH`, `WEBHOOK_SECRET_TOKEN`, `WEBHOOK_EXTERNAL_URL`.
2. Настройте HTTPS и проксирование `POST /webhook/<WEBHOOK_SECRET_PATH>` на сервис.
3. Зарегистрируйте вебхук:
   ```bash
   curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -d url="${WEBHOOK_EXTERNAL_URL}" \
     -d secret_token="${WEBHOOK_SECRET_TOKEN}"
   ```

## Сборка и тесты
```bash
pip install -r requirements-dev.txt
ruff check
black --check .
mypy app
pytest
```

## Структура команд
- `/start` — приветствие и правила.
- `/help` — подсказки по вопросам.
- `/stats`, `/mode friendly|concise`, `/health` — только для админов (обычно Маши).

## Обновление GigaChat token
Клиент автоматически запрашивает access token по `GIGACHAT_AUTH_URL` с Basic-авторизацией (`CLIENT_ID:CLIENT_SECRET`) и поддерживает повторные попытки при кодах 429/5xx.
Токен принудительно обновляется каждые 5 минут (значение можно изменить через `GIGACHAT_TOKEN_FORCE_REFRESH_INTERVAL`), при необходимости предварительно запрашивая новый при `401 Unauthorized`.
Если используете тестовый стенд с самоподписанным сертификатом, выставьте `GIGACHAT_VERIFY_SSL=false` (по умолчанию проверка включена).

## Webhook секретность
- Путь: `/webhook/{WEBHOOK_SECRET_PATH}`.
- Заголовок: `X-Telegram-Bot-Api-Secret-Token = WEBHOOK_SECRET_TOKEN`.
- Рекомендуется размещать за HTTPS-прокси с rate-limit и проверкой IP Telegram.
