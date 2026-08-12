# Dockerfile
FROM python:3.11-slim

# Установка системных утилит для работы с сетью и PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Оптимизация кэширования слоев Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код проекта
COPY . .

# Команда запуска (скрипт автонаката миграций перед стартом описан в README)
CMD ["python", "-m", "main"]
