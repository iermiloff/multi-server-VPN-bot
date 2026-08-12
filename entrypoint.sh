#!/bin/sh
# entrypoint.sh

echo "⏳ Проверяем и накатываем миграции базы данных..."
alembic upgrade head

echo "🚀 Запускаем Telegram-бота..."
exec python -m main
