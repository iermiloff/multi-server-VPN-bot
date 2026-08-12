#!/bin/sh
# entrypoint.sh

# Формируем строку подключения для alembic на основе переменных из .env
DATABASE_URL_ALEMBIC="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# 1. Проверяем, существует ли папка alembic в контейнере
if [ ! -d "alembic" ]; then
    echo "⚙️  Первичный запуск: автоматическая инициализация структуры Alembic..."
    alembic init alembic
    
    # Подменяем target_metadata, явно указывая правильный плоский импорт
    SED_TARGET="target_metadata = None"
    SED_REPLACE="from database.models import Base\ntarget_metadata = Base.metadata"
    sed -i "s|$SED_TARGET|$SED_REPLACE|g" alembic/env.py
    
    echo "📝 Генерируем первую автоматическую миграцию таблиц..."
    # Добавили PYTHONPATH=. для исправления ModuleNotFoundError
    PYTHONPATH=. alembic --config alembic.ini -x db_url="$DATABASE_URL_ALEMBIC" revision --autogenerate -m "auto_saas_init"
fi

echo "⏳ Синхронизируем структуру таблиц с базой данных PostgreSQL..."
# Добавили PYTHONPATH=. для исправления ModuleNotFoundError
PYTHONPATH=. alembic --config alembic.ini -x db_url="$DATABASE_URL_ALEMBIC" upgrade head

echo "🚀 Запускаем Telegram-бота..."
exec python -m main
