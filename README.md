
./scripts/check.bat tests/test_db_chat.py



## 1) Подключиться к БД

Из `.env` возьми `DATABASE_URL` и зайди так:

```bash
cd /root/FairLabQueueBot
source .env
psql "$DATABASE_URL"
```

Если `source .env` не подхватит (из-за формата), можно явно:

```bash
psql "postgresql://fairlab:9bef10a421eb40595b47a0b9ad1a1c45@127.0.0.1:5432/fairlab_queue"
```

(для `psql` обычно формат `postgresql://...`; если с `+psycopg2` не примет, просто убери `+psycopg2`).

---

## 2) Что посмотреть сначала

Внутри `psql`:

```sql
\l
\dt
\d users
\d chats
\d queue
\d queue_history
\d payments
\d swap_requests
\d presence_polls
```

---

## 3) Полезные запросы “что записано”

### Пользователи
```sql
SELECT tg_id, tg_username, real_name, updated_at
FROM users
ORDER BY updated_at DESC
LIMIT 20;
```

### Чаты и подписка
```sql
SELECT chat_id, title, subscription_tier, trial_ends_at, subscription_ends_at, created_at
FROM chats
ORDER BY created_at DESC
LIMIT 20;
```

### Очереди
```sql
SELECT id, chat_id, subject_id, message_id, lesson_date, close_at, status, created_at
FROM queue
ORDER BY created_at DESC
LIMIT 30;
```

### История попыток (для сортировки)
```sql
SELECT tg_id, subject_id, history_position, missed_attempts_count
FROM queue_history
ORDER BY id DESC
LIMIT 30;
```

### Платежи YooKassa
```sql
SELECT yookassa_payment_id, chat_id, tier, amount_rub, status, created_at
FROM payments
ORDER BY created_at DESC
LIMIT 30;
```

### Заявки на обмен
```sql
SELECT id, chat_id, queue_message_id, from_tg_id, to_tg_id, status
FROM swap_requests
ORDER BY id DESC
LIMIT 30;
```

---

## 4) Удобный “читаемый” режим в psql

Перед запросами включи:

```sql
\x on
\pset pager off
```

- `\x on` — вертикальный вид записей (удобно для JSON полей).
- `\pset pager off` — без `less`.

---

## 5) Посмотреть JSON поля красиво

Например, группы чата и extra у очереди:

```sql
SELECT chat_id, jsonb_pretty(groups::jsonb)
FROM chats
WHERE groups IS NOT NULL
LIMIT 10;
```

```sql
SELECT id, jsonb_pretty(extra::jsonb)
FROM queue
WHERE extra IS NOT NULL
ORDER BY id DESC
LIMIT 10;
```

## FairQueueBot

**Для опросов**

Возможные проблемы бота:
- Всё ок
- Иногда приходится перевключать vpn
- Не работает

**Промт, чтобы нейронка поняла смысл ТГ бота**

изучи лучше код бота, пойми что он делает
у нас есть "попытки подхода к преподавателю"
есть "нереализованный слот" - это когда человек не успел подойти, в следующий раз у него дополнительный приоритет, то есть это бонус
и есть средняя позиция в очереди

сортировка происходит так:
чем меньше попыток подхода - тем ты первее
чем больше нереализованных попыток подхода - тем выше приоритет среди подмассива с одинаковым кол-вом попыток подхода
среднее место в очереди - чем больше, тем выше приоритет среди подмассива с одинаковыми нереалдизованными попытками и кол-вом подходов
и потом уже сортируется на рандом.
