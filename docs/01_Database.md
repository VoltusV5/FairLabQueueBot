## Роли

- **Админ**: создаёт очереди, закрывает их, может перемешивать участников.
- **Студент**: записывается на лабораторную через бота, видит своё место в очереди

# Диаграмма БД:

```mermaid
erDiagram
    USER ||--o{ QUEUE_ENTRY : participates
    QUEUE ||--o{ QUEUE_ENTRY : contains
    USER ||--o{ SUBMISSION_ATTEMPT : makes
    SUBJECT ||--o{ QUEUE : has
    SUBJECT ||--o{ SUBMISSION_ATTEMPT : related_to
    USER ||--o{ USERGENERAL : has
    USERGENERAL ||--o{PAY 

    USER {
        int id
        string username
        string first_name
        string last_name
    }
    USERGENERAL {
        int id
        int user_id
    }
    QUEUE {
        int id
        int subject_id
        int chat_id
        datetime date
        datetime close_at
        string status
        int message_id
    }
    QUEUE_ENTRY {
        int id
        int queue_id
        int user_id
        int position
    }
    SUBMISSION_ATTEMPT {
        int id
        int user_id
        int subject_id
        int attempts_count
    }
    SUBJECT {
        int id
        string name
    }
    PAY {
        int id
        int user_id
        bool status
        datetime data_pay
        datetime date_end
    }

```

# Диаграмма БД на русском:

```mermaid
erDiagram
    ПОЛЬЗОВАТЕЛЬ ||--o{ ЗАПИСЬ_В_ОЧЕРЕДЬ : участвует
    ОЧЕРЕДЬ ||--o{ ЗАПИСЬ_В_ОЧЕРЕДЬ : содержит
    ПОЛЬЗОВАТЕЛЬ ||--o{ ПОДХОДЫ_К_СДАЧЕ : делает
    ПРЕДМЕТ ||--o{ ОЧЕРЕДЬ : имеет
    ПРЕДМЕТ ||--o{ ПОДХОДЫ_К_СДАЧЕ : связан

    ПОЛЬЗОВАТЕЛЬ {
        int id
        string username
        string имя
        string фамилия
    }
    ОЧЕРЕДЬ {
        int id
        int предмет_id
        int chat_id
        datetime дата
        datetime закрытие_записи
        string статус
        int сообщение_id
    }
    ЗАПИСЬ_В_ОЧЕРЕДЬ {
        int id
        int очередь_id
        int пользователь_id
        int место
    }
    ПОДХОДЫ_К_СДАЧЕ {
        int id
        int пользователь_id
        int предмет_id
        int сколько_раз_подходил_к_преподу
    }
    ПРЕДМЕТ {
        int id
        string название_предмета
    }
```
