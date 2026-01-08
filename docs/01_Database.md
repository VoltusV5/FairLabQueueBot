## Роли

- **Админ**: создаёт очереди, закрывает их, может перемешивать участников.
- **Студент**: записывается на лабораторную через бота, видит своё место в очереди

# Диаграмма БД:

```mermaid
erDiagram
    USER ||--o{ QUEUE : участвует
    QUEUE ||--o{ SUBMISSION_ATTEMPT : связан
    SUBJECT ||--o{ QUEUE : имеет
    SUBJECT ||--o{ SUBMISSION_ATTEMPT : связан
    USER ||--o{ PAY : имеет

    USER {
        int id
        string real_name
        bool is_admin
    }
    SUBJECT {
        int id
        string name
    }
    QUEUE {
        int id
        int subject_id
        int chat_id
        datetime data
        datetime close_at
        string status
    }
    SUBMISSION_ATTEMPT {
        int id
        int user_id
        int subject_id
        JSON history_position
    }
    PAY {
        int id
        int user_id
        bool status
        datetime date_pay
        datetime date_end
    }


```

# Диаграмма БД на русском:

```mermaid
erDiagram
    ПОЛЬЗОВАТЕЛЬ ||--o{ ОЧЕРЕДЬ : участвует
    ОЧЕРЕДЬ ||--o{ ПОДХОДЫ_К_СДАЧЕ : связан
    ПРЕДМЕТ ||--o{ ОЧЕРЕДЬ : имеет
    ПРЕДМЕТ ||--o{ ПОДХОДЫ_К_СДАЧЕ : связан
    ПОЛЬЗОВАТЕЛЬ ||--o{ ПЛАТЕЖ : имеет

    ПОЛЬЗОВАТЕЛЬ {
        int id
        string real_name
        bool is_admin
    }
    ПРЕДМЕТ {
        int id
        string name
    }
    ОЧЕРЕДЬ {
        int id
        int предмет_id
        int chat_id
        datetime data
        datetime close_at
        string status
    }
    ПОДХОДЫ_К_СДАЧЕ {
        int id
        int пользователь_id
        int предмет_id
        JSON history_position
    }
    ПЛАТЕЖ {
        int id
        int пользователь_id
        bool статус
        datetime дата_оплаты
        datetime дата_окончания
    }

```
