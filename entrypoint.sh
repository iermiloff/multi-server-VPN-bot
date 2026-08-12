#!/bin/sh
# entrypoint.sh

# Формируем строку подключения для alembic на основе переменных из .env
DATABASE_URL_ALEMBIC="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# 1. Проверяем, существует ли папка alembic в репозитории
if [ ! -d "alembic" ]; then
    echo "⚙️  Первичный запуск: автоматическая инициализация структуры Alembic..."
    alembic init alembic
    
    # Автоматически подменяем target_metadata в сгенерированном env.py
    # Чтобы alembic знал, где лежат наши таблицы models.py
    SED_TARGET="target_metadata = None"
    SED_REPLACE="from database.models import Base\ntarget_metadata = Base.metadata"
    
    # Используем sed для безопасной замены строк внутри контейнера
    sed -i "s|$SED_TARGET|$SED_REPLACE|g" alembic/env.py
    
    echo "📝 Генерируем первую автоматическую миграцию таблиц..."
    alembic --config alembic.ini -x db_url="$DATABASE_URL_ALEMBIC" revision --autogenerate -m "auto_saas_init"
fi

echo "⏳ Синхронизируем структуру таблиц с базой данных PostgreSQL..."
alembic --config alembic.ini -x db_url="$DATABASE_URL_ALEMBIC" upgrade head

echo "🚀 Запускаем Telegram-бота..."
exec python -m main
