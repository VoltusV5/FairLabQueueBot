def is_user_superadmin(func):
    """Декоратор для проверки супер админ пользователь или нет.
    Применяется для функций, с которыми может работать только супер админ
    """
    async def wrapper(message: Message, *args, **kwargs):
        user_message = message.from_user
        if user_message is None or user_message.id not in config.bot.admin_ids:
            await message.reply("Недостаточно прав доступа, вы не супер админ")
            return
        return await func(message, *args, **kwargs)
    return wrapper


def is_user_admin(func):
    """Декоратор для проверки админ пользователь или нет.
    Применяется для функций, с которыми может работать только супер админ
    """
    async def wrapper(message: Message, *args, **kwargs):
        user = message.from_user
        chat_id = message.chat.id
        if user is None:
            return
        user_data = get_user(chat_id, user.id)
        # Если пользователь не админ и не супер админ, то у него нет доступа
        if (not user_data["is_admin"]
                and user_data["id"] not in config.bot.admin_ids):
            await message.reply("Недостаточно прав доступа, вы не админ")
            return
        return await func(message, *args, **kwargs)
    return wrapper


# Этот хэндлер срабатывает на команду /setadmin
@router.message(Command(commands='setadmin'))
@is_user_superadmin
async def process_setadmin_command(message: Message):
    """Позволяет супер админу назначать админов.
    Назначает пользователя админом из-за декоратора is_user_superadmin
    """
    pass


# Сортировка
'''queue = sorted(
    queue,
    key=lambda x: len(x[1]),
    reverse=False
)
# Список, по которому можно понять сколько раз длина встречается
print(queue)
cnt = [0] * (len(queue[-1][1]) + 1)
for element in queue:
    current_len = len(element[1])
    cnt[current_len] += 1
print(cnt)
# Накопительная сумма предыдущего списка
for i in range(1, len(cnt)):
    cnt[i] = cnt[i] + cnt[i - 1]
print(cnt)

max_symbol_len = max([i[2] for i in queue])

# Сколько раз встречается *
cnt2 = [0] * (max_symbol_len + 1)
for element in queue:
    cnt2[element[2]] += 1
print(cnt2)
for i in range(1, len(cnt2)):
    cnt2[i] = cnt2[i] + cnt2[i - 1]
print(cnt2)

max_digit = max(cnt2)

# Накопительная сумма предыдущего списка
cnt2 = cnt2[::-1]
for i in range(len(cnt2)):
    cnt2[i] = max_digit - cnt2[i]


final = []

for i in range(len(cnt)):
    if not cnt[i] > 0:
        continue
    if i == 0:
        current_queue = queue[0:cnt[i]]
    else:
        current_queue = queue[cnt[i - 1]:cnt[i]]
    current_queue = sorted(
        current_queue,
        key=lambda x: x[2],
        reverse=True
    )

    current_queue = sorted(
        current_queue,
        key=lambda x: sum(x[1]) / len(x[1]),
        reverse=True
    )
    final += current_queue
print(final)'''


'''
# Индекс элемента, который хранит кол-во раз когда человек не успел сдать
NUM_OF_MISSED_ATTEMP = 2


def check_people(queue) -> str:
    """Функция которая обработает людей которые не успели сдать в прошлый раз 
    Она создаёт список из людей у которых есть пометки со *"""
    spisok = ""
    queue_new = []
    for indx, history in enumerate(queue):
        if isinstance(history[NUM_OF_MISSED_ATTEMP], str):
            pos = int(history[1][-1].replace("*", ""))
            queue_new.append((history[0], pos))
            del queue[indx]

    queue_new = sorted(queue_new, key=lambda x: x[1])
    if queue_new != []:
        for i in range(len(queue_new)):
            spisok += f"{i+1}. {queue_new[i][0]}\n"

    return spisok
'''


# Добавление пользователей в БД Submittion attempt
'''
            positions = [[
                i.split('.')[0],
                i.split(" ")[-1].replace("(", "").replace(")", "")]
                for i in queue.split("\n")]

            while ['', ''] in positions:
                positions.remove(['', ''])
            print("!", positions)

            # Добавляем каждому участнику запись в историю записей
            add_history_position(
                positions,
                get_subject_id(callback.message.chat.id, message_subject)
            )'''
