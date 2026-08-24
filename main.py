import telebot
from telebot import types
from random import randint
import random
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json

BOT_TOKEN = 'Your_token'


with open("kubrik.json", 'r') as f:
    data = json.load(f)
    USER_GROUPS = data
# Создаем обратный словарь для быстрого поиска группы по ID пользователя
GROUP_BY_USER = {}
for group_num, users in USER_GROUPS.items():
    for user_id in users:
        GROUP_BY_USER[user_id] = group_num



ADMIN_IDS = []  # Список ID администраторов
Users = []

with open("users.json", 'r') as f:
    data = json.load(f)

    for p in data:
        if p == "user":
            i = data.get("user")
            for t in i:
                Users.append(t)
        elif p == "admin":
            i = data.get("admin")
            for t in i:
                ADMIN_IDS.append(t)

TIMEZONE_OFFSET = timedelta(hours=12)

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN)


# Функции для работы с временем
def get_current_time() -> datetime:
    return datetime.utcnow() + TIMEZONE_OFFSET


def format_datetime(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')


# Подключение к базе данных
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect('users.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Таблица пользователей
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            balance INTEGER DEFAULT 0 NOT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now', '+12 hours'))
        )''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                request_date TIMESTAMP DEFAULT (datetime('now', '+12 hours')),
                status TEXT DEFAULT 'pending',
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(service_id) REFERENCES services(id)
            )
        ''')

        # Таблица услуг
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            price INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now', '+12 hours')),
            UNIQUE(name)
        )''')

        # Таблица покупок
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            purchase_date TIMESTAMP DEFAULT (datetime('now', '+12 hours')),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(service_id) REFERENCES services(id)
        )''')

        # Таблица операций с балансом
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS balance_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            operation_type TEXT NOT NULL,  -- 'add' или 'deduct'
            description TEXT,
            operation_date TIMESTAMP DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')


# Новый класс для работы с ожидающими покупками
class PurchaseManager:
    @staticmethod
    def get_purchase(purchase_id: int) -> Optional[Dict]:
        """Получить информацию о покупке по её ID"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    pp.*,
                    u.username as user_username,
                    s.name as service_name,
                    s.price as service_price
                FROM pending_purchases pp
                LEFT JOIN users u ON pp.user_id = u.id
                LEFT JOIN services s ON pp.service_id = s.id
                WHERE pp.id = ?
            ''', (purchase_id,))
            result = cursor.fetchone()
            if not result:
                return None
            purchase = dict(result)
            # Конвертация времени
            purchase['request_date'] = format_datetime(
                datetime.strptime(purchase['request_date'], '%Y-%m-%d %H:%M:%S')
            )
            return purchase
    @staticmethod
    def add_pending_purchase(user_id: int, service_id: int):
        with get_db_connection() as conn:
            conn.execute(
                'INSERT INTO pending_purchases (user_id, service_id) VALUES (?, ?)',
                (user_id, service_id)
            )
            return conn.execute('SELECT last_insert_rowid()').fetchone()[0]

    @staticmethod
    def handle_purchase(purchase_id: int, approve: bool):
        with get_db_connection() as conn:
            conn.execute('BEGIN TRANSACTION')
            try:
                # Получаем данные о покупке
                purchase = conn.execute(
                    'SELECT * FROM pending_purchases WHERE id = ?',
                    (purchase_id,)
                ).fetchone()

                if not purchase:
                    return False

                if approve:
                    service = ServiceManager.get_service(purchase['service_id'])
                    if not service:
                        return False

                    # Выполняем покупку
                    conn.execute(
                        'UPDATE users SET balance = balance - ? WHERE id = ?',
                        (service['price'], purchase['user_id'])
                    )
                    conn.execute(
                        'INSERT INTO purchases (user_id, service_id) VALUES (?, ?)',
                        (purchase['user_id'], purchase['service_id'])
                    )

                # Обновляем статус
                conn.execute(
                    'UPDATE pending_purchases SET status = ? WHERE id = ?',
                    ('approved' if approve else 'rejected', purchase_id)
                )
                conn.commit()
                return True
            except:
                conn.rollback()
                return False

# Класс для работы с пользователями
class UserManager:
    @staticmethod
    def add_user(user: types.User):
        with get_db_connection() as conn:
            conn.execute(
                'INSERT OR IGNORE INTO users (id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                (user.id, user.username, user.first_name, user.last_name)
            )

    @staticmethod
    def get_user(user_id: int) -> Optional[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT *, datetime(created_at, "localtime") as local_created_at FROM users WHERE id = ?',
                           (user_id,))
            result = cursor.fetchone()
            if result:
                user = dict(result)
                user['created_at'] = user['local_created_at']
                return user
            return None

    @staticmethod
    def get_balance(user_id: int) -> int:
        user = UserManager.get_user(user_id)
        return user['balance'] if user else 0

    @staticmethod
    def update_balance(user_id: int, amount: int):
        with get_db_connection() as conn:
            conn.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user_id))

    @staticmethod
    def can_afford(user_id: int, price: int) -> bool:
        return UserManager.get_balance(user_id) >= price

    @staticmethod
    def get_all_users() -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT *, datetime(created_at, "localtime") as local_created_at FROM users ORDER BY created_at DESC')
            users = []
            for row in cursor.fetchall():
                user = dict(row)
                user['created_at'] = user['local_created_at']
                users.append(user)
            return users

    @staticmethod
    def record_balance_operation(user_id: int, admin_id: int, amount: int, operation_type: str,
                                 description: str = ""):
        with get_db_connection() as conn:
            conn.execute(
                'INSERT INTO balance_operations (user_id, admin_id, amount, operation_type, description) '
                'VALUES (?, ?, ?, ?, ?)',
                (user_id, admin_id, abs(amount), operation_type, description)
            )


# Класс для работы с услугами
class ServiceManager:
    @staticmethod
    def delete_service(service_id: int) -> bool:
        with get_db_connection() as conn:
            try:
                conn.execute('BEGIN TRANSACTION')
                # Удаляем связанные покупки
                conn.execute('DELETE FROM purchases WHERE service_id = ?', (service_id,))
                # Удаляем саму услугу
                conn.execute('DELETE FROM services WHERE id = ?', (service_id,))
                conn.commit()
                return True
            except:
                conn.rollback()
                return False
    @staticmethod
    def add_service(name: str, description: str, price: int):
        with get_db_connection() as conn:
            try:
                conn.execute(
                    'INSERT INTO services (name, description, price) VALUES (?, ?, ?)',
                    (name.strip(), description.strip(), price)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    @staticmethod
    def get_all_services() -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Убираем конвертацию в localtime, так как время уже хранится в GMT+12
            cursor.execute('''
                SELECT *, datetime(created_at) as created_at 
                FROM services 
                ORDER BY name
            ''')
            services = []
            for row in cursor.fetchall():
                service = dict(row)
                # Форматируем время с указанием часового пояса
                service['created_at'] = format_datetime(
                    datetime.strptime(service['created_at'], '%Y-%m-%d %H:%M:%S'))
                services.append(service)
            return services

    @staticmethod
    def get_service(service_id: int) -> Optional[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Аналогичное исправление для получения одной услуги
            cursor.execute('''
                SELECT *, datetime(created_at) as created_at 
                FROM services 
                WHERE id = ?
            ''', (service_id,))
            result = cursor.fetchone()
            if result:
                service = dict(result)
                service['created_at'] = format_datetime(
                    datetime.strptime(service['created_at'], '%Y-%m-%d %H:%M:%S'))
                return service
            return None

    @staticmethod
    def purchase_service(user_id: int, service_id: int) -> bool:
        service = ServiceManager.get_service(service_id)
        if not service:
            return False

        if not UserManager.can_afford(user_id, service['price']):
            return False

        with get_db_connection() as conn:
            try:
                conn.execute('BEGIN TRANSACTION')
                conn.execute(
                    'UPDATE users SET balance = balance - ? WHERE id = ?',
                    (service['price'], user_id))
                conn.execute(
                    'INSERT INTO purchases (user_id, service_id) VALUES (?, ?)',
                    (user_id, service_id))
                conn.commit()
                return True
            except:
                conn.rollback()
                return False


# Класс для работы со статистикой
class StatsManager:
    @staticmethod
    def get_user_count() -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM users')
            return cursor.fetchone()[0]


    @staticmethod
    def get_total_balance() -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT SUM(balance) FROM users')
            return cursor.fetchone()[0] or 0

    @staticmethod
    def get_service_count() -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM services')
            return cursor.fetchone()[0]

    @staticmethod
    def get_purchase_count() -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM purchases')
            return cursor.fetchone()[0]
    @staticmethod
    def get_recent_purchases(limit: int = 5) -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT p.id, u.username, s.name, 
                       datetime(p.purchase_date, "localtime") as local_purchase_date 
                FROM purchases p
                JOIN users u ON p.user_id = u.id
                JOIN services s ON p.service_id = s.id
                ORDER BY p.purchase_date DESC
                LIMIT ?
            ''', (limit,))
            purchases = []
            for row in cursor.fetchall():
                purchase = dict(row)
                purchase['purchase_date'] = purchase['local_purchase_date']
                purchases.append(purchase)
            return purchases
    @staticmethod
    def get_recent_balance_operations(limit: int = 5) -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Используем LEFT JOIN и правильные алиасы
            cursor.execute('''
                SELECT 
                    b.id,
                    b.user_id,
                    b.admin_id,
                    COALESCE(u.username, 'Система') as user,
                    COALESCE(a.username, 'Админ') as admin,
                    b.amount,
                    b.operation_type,
                    b.description,
                    datetime(b.operation_date, 'localtime') as operation_date
                FROM balance_operations b
                LEFT JOIN users u ON b.user_id = u.id
                LEFT JOIN users a ON b.admin_id = a.id
                ORDER BY b.operation_date DESC
                LIMIT ?
            ''', (limit,))
            operations = []
            for row in cursor.fetchall():
                operation = dict(row)
                # Форматируем дату
                operation['operation_date'] = format_datetime(
                    datetime.strptime(operation['operation_date'], '%Y-%m-%d %H:%M:%S')
                )
                operations.append(operation)
            return operations


# Создание клавиатуры
def create_keyboard(is_admin: bool = False) -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)

    common_buttons = [
        types.KeyboardButton("👤 Профиль"),
        types.KeyboardButton("📋 Просмотреть услуги"),
        types.KeyboardButton("🛒 Купить услугу")
    ]

    if is_admin:
        admin_buttons = [
            types.KeyboardButton("👤 Управление пользователями"),
            types.KeyboardButton("⚙️ Управление услугами"),
            types.KeyboardButton("📊 Статистика")
        ]
        keyboard.add(*admin_buttons)
    else:
        keyboard.add(*common_buttons)

    return keyboard

@bot.message_handler(commands=['start'])
def handle_start(message: types.Message):
    is_admin = message.from_user.id in ADMIN_IDS
    is_user = message.from_user.id in Users
    if is_admin:
        greeting = "👋 Привет, Админ!"
    elif is_user:
        greeting = "👋 Привет!"
        UserManager.add_user(message.from_user)
    else:
        greeting = 'Вас нет в базе данных'
    bot.send_message(
        message.chat.id,
        greeting,
        reply_markup=create_keyboard(is_admin)
    )


@bot.message_handler(func=lambda m: m.from_user.id in Users and m.text == "👤 Профиль")
def handle_profile(message: types.Message):
    user = UserManager.get_user(message.from_user.id)
    group_number = GROUP_BY_USER.get(message.from_user.id, "Не состоит в кубрике")
    a = randint(0, 1000)
    if a == 356:
        bot.send_message(message.from_user.id, "Создатель бота @Kto_to356")

    profile_info = (
        f"👤 Ваш профиль:\n\n"
        f"▫️ ID: {user['id']}\n"
        f"▫️ Имя: {user['first_name']} {user['last_name'] or ''}\n"
        f"▫️ Никнейм: @{user['username'] or 'не указан'}\n"
        f"▫️ Кубрик: {group_number}\n"
        f"▫️ Баланс: {user['balance']} монет\n"
        f"▫️ Дата регистрации: {user['created_at']}"
    )

    bot.send_message(message.chat.id, profile_info)


@bot.message_handler(func=lambda m: m.from_user.id in Users and m.text == "📋 Просмотреть услуги")
def handle_services_list(message: types.Message):
    services = ServiceManager.get_all_services()
    if not services:
        bot.send_message(message.chat.id, "ℹ️ На данный момент услуги отсутствуют.")
        return

    response = ["📋 Доступные услуги:", ""]
    for service in services:
        response.append(
            f"🔹 {service['name']}\n"
            f"   Описание: {service['description']}\n"
            f"   Цена: {service['price']} монет\n"
            f"   ID: {service['id']}\n"
            f"   Добавлено: {service['created_at']}\n"
        )

    bot.send_message(message.chat.id, "\n".join(response))


@bot.message_handler(func=lambda m: m.from_user.id in Users and m.text == "🛒 Купить услугу")
def handle_buy_service(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Введите ID услуги, которую хотите приобрести:",
        reply_markup=types.ForceReply(selective=True)
    )


@bot.message_handler(
    func=lambda m: m.reply_to_message and
                   m.reply_to_message.text == "Введите ID услуги, которую хотите приобрести:"
)
def handle_service_id_input(message: types.Message):
    user_id = message.from_user.id
    try:
        service_id = int(message.text.strip())
        service = ServiceManager.get_service(service_id)

        if not service:
            bot.send_message(message.chat.id, "❌ Услуга не найдена")
            return

        if user_id not in ADMIN_IDS:
            # Для админов - запрос подтверждения
            purchase_id = PurchaseManager.add_pending_purchase(user_id, service_id)

            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("✅ Подтвердить",
                                           callback_data=f"approve_{purchase_id}"),
                types.InlineKeyboardButton("❌ Отклонить",
                                           callback_data=f"reject_{purchase_id}")
            )
            r = random.randint(0, len(ADMIN_IDS) - 1)  # rand от 0 до len(tab)-1 (т.к. Python индексирует с 0)
            admin_id = ADMIN_IDS[r]
            bot.send_message(
                admin_id,
                f"⚠️ Требуется подтверждение покупки:\n"
                f"Пользователь: @{message.from_user.username}\n"
                f"Услуга: {service['name']}\n"
                f"Цена: {service['price']} монет",
                reply_markup=markup
            )

            bot.send_message(
                message.chat.id,
                "⏳ Ваш запрос отправлен на подтверждение другим администраторам"
            )
        else:
            # Для обычных пользователей - обычная покупка
            if ServiceManager.purchase_service(user_id, service_id):
                bot.send_message(message.chat.id, "✅ Покупка успешно завершена")
            else:
                bot.send_message(message.chat.id, "❌ Ошибка при покупке")

    except ValueError:
        bot.send_message(message.chat.id, "❌ Некорректный ID услуги")



# команды админов
@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "📊 Статистика")
def handle_stats(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Введите количество последних записей для отображения (по умолчанию 5):",
        reply_markup=types.ForceReply(selective=True)
    )

@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.reply_to_message and
                     "количество последних записей" in m.reply_to_message.text)
def handle_stats_with_limit(message: types.Message):
    try:
        limit = int(message.text.strip()) if message.text.strip() else 5
        if limit <= 0:
            raise ValueError
    except ValueError:
        limit = 5
        bot.send_message(message.chat.id, "❌ Некорректное число. Использую значение по умолчанию: 5.")

    stats = [
        f"📊 Статистика системы:",
        f"👥 Пользователей: {StatsManager.get_user_count()}",
        f"💵 Общий баланс: {StatsManager.get_total_balance()} монет",
        f"🛍 Услуг: {StatsManager.get_service_count()}",
        f"🛒 Покупок: {StatsManager.get_purchase_count()}",
        f"🕒 Текущее время сервера: {format_datetime(get_current_time())}",
        "",
        f"Последние {limit} покупок:"
    ]

    purchases = StatsManager.get_recent_purchases(limit)
    if purchases:
        for purchase in purchases:
            stats.append(
                f"🔹 {purchase['username'] or 'Пользователь'} → {purchase['name']}\n"
                f"   Дата: {purchase['purchase_date']}"
            )
    else:
        stats.append("ℹ️ Покупок не найдено")

    stats.extend(["", f"Последние {limit} операции с балансом:"])

    operations = StatsManager.get_recent_balance_operations(limit)
    if operations:
        for op in operations:
            stats.append(
                f"🔹 {'➕' if op['operation_type'] == 'add' else '➖'} {op['amount']} монет\n"
                f"   Пользователь: {'@' + op['user'] or 'ID'}\n"
                f"   Дата: {op['operation_date']}\n"
                f"   Причина: {op['description'] or 'Не указана'}"
            )
    else:
        stats.append("ℹ️ Операций с балансом не найдено")

    bot.send_message(message.chat.id, "\n".join(stats))


@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "👤 Управление пользователями")
def handle_manage_users(message: types.Message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("📝 Изменить баланс пользователя"),
        types.KeyboardButton("👥 Список пользователей"),
        types.KeyboardButton("🔙 Назад"),
    )
    bot.send_message(
        message.chat.id,
        "Выберите действие:",
        reply_markup=markup
    )

@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "🔙 Назад")
def handle_back(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Главное меню:",
        reply_markup=create_keyboard(True)
    )

@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "👥 Список пользователей")
def handle_users_list(message: types.Message):
    users = UserManager.get_all_users()
    if not users:
        bot.send_message(message.chat.id, "ℹ️ Пользователей пока нет.")
        return

    response = ["👥 Список пользователей:", ""]
    for user in users:
        group_info = GROUP_BY_USER.get(user['id'], 'Нет')
        response.append(
            f"🔹 ID: {user['id']}\n"
            f"   Кубрик: {group_info}\n"
            f"   Имя: {user['first_name']} {user['last_name'] or ''}\n"
            f"   Ник: @{user['username'] or 'нет'}\n"
            f"   Баланс: {user['balance']} монет\n"
            f"   Регистрация: {user['created_at']}\n"
        )

    bot.send_message(message.chat.id, "\n".join(response))


@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "📝 Изменить баланс пользователя")
def handle_modify_balance(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Введите ID пользователя и сумму изменения через пробел (например: 123456 100 для добавления или 123456 -50 для списания):",
        reply_markup=types.ForceReply(selective=True)
    )


@bot.message_handler(func=lambda
        m: m.from_user.id in ADMIN_IDS and m.reply_to_message and m.reply_to_message.text == "Введите ID пользователя и сумму изменения через пробел (например: 123456 100 для добавления или 123456 -50 для списания):")
def handle_balance_modification(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError

        user_id = int(parts[0])
        amount = int(parts[1])

        user = UserManager.get_user(user_id)
        if not user:
            bot.send_message(message.chat.id, "❌ Пользователь с таким ID не найден.")
            return

        UserManager.update_balance(user_id, amount)
        operation_type = 'add' if amount > 0 else 'deduct'
        UserManager.record_balance_operation(
            user_id, message.from_user.id, abs(amount), operation_type,
            f"Изменение администратором {message.from_user.username or message.from_user.id}"
        )

        new_balance = UserManager.get_balance(user_id)
        bot.send_message(
            message.chat.id,
            f"✅ Баланс пользователя {user['first_name']} (@{user['username'] or 'нет'}) успешно изменен.\n"
            f"Изменение: {'+' if amount > 0 else ''}{amount} монет\n"
            f"Новый баланс: {new_balance} монет\n"
            f"Дата операции: {format_datetime(get_current_time())}"
        )
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Неверный формат. Пожалуйста, используйте:\n"
            "<ID пользователя> <сумма изменения>\n"
            "Пример: 123456 100 (добавить 100 монет)\n"
            "Пример: 123456 -50 (списать 50 монет)"
        )


@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "⚙️ Управление услугами")
def handle_manage_services(message: types.Message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("➕ Добавить услугу"),
        types.KeyboardButton("📋 Список услуг"),
        types.KeyboardButton("🗑️ Удалить услугу"),  # Новая кнопка
        types.KeyboardButton("🔙 Назад")
    )
    bot.send_message(
        message.chat.id,
        "Выберите действие:",
        reply_markup=markup
    )


@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "➕ Добавить услугу")
def handle_add_service(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Введите данные новой услуги в формате:\n"
        "<название>, <описание>, <цена>\n\n"
        "Пример: Видеомонтаж, Профессиональный монтаж видео, 100",
        reply_markup=types.ForceReply(selective=True)
    )

@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "📋 Список услуг")
def handle_services_list(message: types.Message):
    services = ServiceManager.get_all_services()
    if not services:
        bot.send_message(message.chat.id, "ℹ️ На данный момент услуги отсутствуют.")
        return

    response = ["📋 Доступные услуги:", ""]
    for service in services:
        response.append(
            f"🔹 {service['name']}\n"
            f"   Описание: {service['description']}\n"
            f"   Цена: {service['price']} монет\n"
            f"   ID: {service['id']}\n"
            f"   Добавлено: {service['created_at']}\n"  # Теперь с правильным GMT+12
        )

    bot.send_message(message.chat.id, "\n".join(response))


@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and m.text == "🗑️ Удалить услугу")
def handle_delete_service_prompt(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Введите ID услуги для удаления:",
        reply_markup=types.ForceReply(selective=True)
    )


@bot.message_handler(func=lambda
        m: m.from_user.id in ADMIN_IDS and m.reply_to_message and m.reply_to_message.text == "Введите ID услуги для удаления:")
def handle_delete_service_input(message: types.Message):
    try:
        service_id = int(message.text.strip())
        service = ServiceManager.get_service(service_id)
        if not service:
            bot.send_message(message.chat.id, "❌ Услуга с таким ID не найдена.")
            return

        if ServiceManager.delete_service(service_id):
            bot.send_message(
                message.chat.id,
                f"✅ Услуга '{service['name']}' и все связанные покупки успешно удалены."
            )
        else:
            bot.send_message(message.chat.id, "❌ Ошибка при удалении услуги.")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Пожалуйста, введите корректный ID услуги (число).")


@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_', 'reject_')))
def handle_purchase_confirmation(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ Доступ запрещен")
        return

    purchase_id = call.data.split('_')[1]
    purchase = PurchaseManager.get_purchase(purchase_id)

    if not purchase:
        bot.answer_callback_query(call.id, "❌ Покупка не найдена")
        return

    approve = call.data.startswith('approve_')

    if PurchaseManager.handle_purchase(purchase_id, approve):
        status = "ᴨодᴛʙᴇᴩждᴇнᴀ" if approve else "оᴛᴋᴧонᴇнᴀ"
        admin_name = call.from_user.username or call.from_user.first_name

        # Обновляем сообщение у администраторов
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"🛒 Покупка {status} администратором @{admin_name}\n"
                 f"Пользователь: @{purchase['user_username']}\n"
                 f"Услуга: {purchase['service_name']}\n"
                 f"Цена: {purchase['service_price']} монет\n"
                 f"Время запроса: {purchase['request_date']}",
            reply_markup=None
        )

        # Уведомление покупателю
        bot.send_message(
            purchase['user_id'],
            f"📢 Ваша покупка услуги '{purchase['service_name']}' "
            f"на сумму {purchase['service_price']} монет {status}!"
        )
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка обработки запроса")

@bot.message_handler(func=lambda
        m: m.from_user.id in ADMIN_IDS and m.reply_to_message and "Введите данные новой услуги" in m.reply_to_message.text)
def handle_new_service_input(message: types.Message):
    try:
        name, description, price = map(str.strip, message.text.split(",", 2))
        price = int(price)

        if ServiceManager.add_service(name, description, price):
            bot.send_message(
                message.chat.id,
                f"✅ Услуга '{name}' успешно добавлена!\n"
                f"Дата добавления: {format_datetime(get_current_time())}"
            )
        else:
            bot.send_message(message.chat.id, "❌ Услуга с таким названием уже существует.")
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Неверный формат. Пожалуйста, используйте:\n"
            "<название>, <описание>, <цена>"
        )

if __name__ == '__main__':
    init_db()  # Инициализация базы данных
    print(f"Бот запущен... Время запуска: {format_datetime(get_current_time())}")
    bot.infinity_polling()
