# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

# Запрет создания .pyc файлов и включение небуферизованного вывода stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# Установка базовых системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Создание непривилегированного пользователя для безопасности
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -m -s /bin/sh appuser

# Установка Python-зависимостей с кэшированием слоя
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir -r requirements.txt

# Копирование скрипта запуска и исходного кода
COPY --chown=appuser:appuser entrypoint.sh .
RUN chmod +x entrypoint.sh

COPY --chown=appuser:appuser alembic.ini .
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser bot.py config.py create_dp.py ./
COPY --chown=appuser:appuser src ./src

# Переключение на непривилегированного пользователя
USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "bot.py"]
