#!/bin/sh
set -e

# Функция ожидания готовности базы данных через чистый Python (без внешних утилит)
wait_for_db() {
    python - <<'EOF'
import os
import sys
import time
import socket
import urllib.parse

db_url = (os.environ.get("DATABASE_URL") or "").strip()
if not db_url:
    print("entrypoint: DATABASE_URL не задан, пропускаем ожидание БД.")
    sys.exit(0)

# Приводим к стандартному виду URL для извлечения хоста и порта
clean_url = db_url.replace("postgresql+asyncpg://", "http://").replace("postgresql://", "http://").replace("postgres://", "http://")
try:
    parsed = urllib.parse.urlparse(clean_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
except Exception as e:
    print(f"entrypoint: Не удалось распарсить DATABASE_URL: {e}")
    sys.exit(0)

print(f"entrypoint: Ожидание доступности PostgreSQL ({host}:{port})...")
max_attempts = 30
for attempt in range(1, max_attempts + 1):
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"entrypoint: База данных {host}:{port} доступна!")
            sys.exit(0)
    except OSError:
        if attempt % 5 == 0:
            print(f"entrypoint: Ожидание базы... попытка {attempt}/{max_attempts}")
        time.sleep(1)

print("entrypoint: Предупреждение: БД не ответила в течение 30 секунд. Продолжаем запуск...")
EOF
}

# Если мы запускаем бота или команду по умолчанию, проверяем БД и применяем миграции
if [ "$1" = "python" ] && [ "$2" = "bot.py" ]; then
    wait_for_db

    if [ "${SKIP_MIGRATIONS:-0}" != "1" ] && [ "${SKIP_MIGRATIONS:-0}" != "true" ]; then
        echo "entrypoint: Применение миграций Alembic (alembic upgrade head)..."
        alembic upgrade head
    else
        echo "entrypoint: SKIP_MIGRATIONS задан, пропускаем миграции."
    fi
fi

echo "entrypoint: Запуск команды: $@"
exec "$@"
