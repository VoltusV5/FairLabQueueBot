## Роли

- **Админ**: создаёт очереди, закрывает их, может перемешивать участников.
- **Студент**: записывается на лабораторную через бота, видит своё место в очереди

# Диаграмма БД:

```mermaid
erDiagram
    USER ||--o{ SUBMISSION_ATTEMPT : имеет
    USER ||--o{ PAY : имеет
    SUBJECT ||--o{ QUEUE : имеет
    SUBJECT ||--o{ SUBMISSION_ATTEMPT : имеет

    USER {
        int id PK
        string tg_username UK
        int tg_id
        string real_name
        bool is_admin
    }
    SUBJECT {
        int id PK
        int chat_id
        string subject_name
    }
    QUEUE {
        int id PK
        int subject_id FK
        int chat_id
        int message_id
        datetime lesson_date
        datetime close_at
        string status
        list usernames
    }
    SUBMISSION_ATTEMPT {
        int id PK
        str tg_username FK
        int subject_id FK
        JSON history_position
    }
    PAY {
        int id PK
        int chat_id FK
        string status
        string subscription_level
        datetime date_pay
    }
    CHAT {
        int id PK
        int chat_id FK
        string subscription_level
        datetime subscription_start_date
        datetime subscription_end_date
    }
```
