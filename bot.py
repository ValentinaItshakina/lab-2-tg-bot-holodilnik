import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настрой свой токен Telegram-бота
TOKEN = "8305788233:AAEJS6xoXtpUg_7OEHDR757Bj2IPDRks9EY"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("products_bot.db")
    cursor = conn.cursor()

    # Таблица продуктов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            added_date TEXT,
            expiry_date TEXT,
            reminder_interval INTEGER,
            last_reminded TEXT
        )
    """)

    # Таблица рецептов (с примерами)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            ingredients TEXT,
            instructions TEXT
        )
    """)

    # Добавим несколько базовых рецептов для примера, если таблица пуста
    cursor.execute("SELECT COUNT(*) FROM recipes")
    if cursor.fetchone()[0] == 0:
        sample_recipes = [
            ("Омлет с сыром", "яйца, молоко, сыр",
             "Взбейте яйца с молоком, вылейте на сковороду, посыпьте сыром и жарьте 5 минут."),
            ("Овощное рагу", "картофель, морковь, лук, кабачок",
             "Нарежьте овощи кубиками и тушите на среднем огне до готовности."),
            ("Бутерброд с сыром и колбасой", "хлеб, колбаса, сыр",
             "Положите сыр и колбасу на хлеб и разогрейте в микроволновке.")
        ]
        cursor.executemany("INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)", sample_recipes)

    conn.commit()
    conn.close()


init_db()


# --- МАШИНА СОСТОЯНИЙ (FSM) для добавления продукта ---
class AddProduct(StatesGroup):
    name = State()
    expiry = State()
    interval = State()


# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📦 Мои продукты")
    builder.button(text="⚠️ Просроченные")
    builder.button(text="➕ Добавить продукт")
    builder.button(text="🍳 Подобрать рецепт")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)


# --- ХЕНДЛЕРЫ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот для отслеживания продуктов и подбора рецептов.\n"
        "Я помогу следить за сроками годности, удалять съеденное и подскажу, что приготовить.",
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == "➕ Добавить продукт")
async def start_add_product(message: types.Message, state: FSMContext):
    await message.answer("Введите название продукта:")
    await state.set_state(AddProduct.name)


@dp.message(AddProduct.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(
        "Введите срок годности в формате **ДД.ММ.ГГГГ**\n"
        "Или отправьте `-`, если срока годности нет.",
        parse_mode="Markdown"
    )
    await state.set_state(AddProduct.expiry)


@dp.message(AddProduct.expiry)
async def process_expiry(message: types.Message, state: FSMContext):
    text = message.text.strip()
    added_date = datetime.now().strftime("%Y-%m-%d")

    if text == "-":
        await state.update_data(expiry_date=None)
        await message.answer("Укажите интервал напоминаний (в днях), например: `3`", parse_mode="Markdown")
        await state.set_state(AddProduct.interval)
    else:
        try:
            expiry_dt = datetime.strptime(text, "%d.%m.%Y")
            await state.update_data(expiry_date=expiry_dt.strftime("%Y-%m-%d"), interval=None)

            data = await state.get_data()
            conn = sqlite3.connect("products_bot.db")
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO products (user_id, name, added_date, expiry_date, reminder_interval, last_reminded) VALUES (?, ?, ?, ?, ?, ?)",
                (message.from_user.id, data["name"], added_date, expiry_dt.strftime("%Y-%m-%d"), None, added_date)
            )
            conn.commit()
            conn.close()

            await message.answer(f"Продукт «{data['name']}» успешно добавлен!", reply_markup=get_main_keyboard())
            await state.clear()
        except ValueError:
            await message.answer("Неверный формат даты. Пожалуйста, используйте ДД.ММ.ГГГГ или отправьте `-`.")


@dp.message(AddProduct.interval)
async def process_interval(message: types.Message, state: FSMContext):
    try:
        interval = int(message.text.strip())
        data = await state.get_data()
        added_date = datetime.now().strftime("%Y-%m-%d")

        conn = sqlite3.connect("products_bot.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO products (user_id, name, added_date, expiry_date, reminder_interval, last_reminded) VALUES (?, ?, ?, ?, ?, ?)",
            (message.from_user.id, data["name"], added_date, None, interval, added_date)
        )
        conn.commit()
        conn.close()

        await message.answer(f"Продукт «{data['name']}» добавлен! Буду напоминать о нем каждые {interval} дн.",
                             reply_markup=get_main_keyboard())
        await state.clear()
    except ValueError:
        await message.answer("Пожалуйста, введите целое число дней (например, `3`).")


@dp.message(F.text == "📦 Мои продукты")
async def show_products(message: types.Message):
    conn = sqlite3.connect("products_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, added_date, expiry_date, reminder_interval FROM products WHERE user_id = ?",
                   (message.from_user.id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("Ваш список продуктов пуст.")
        return

    today = datetime.now().date()
    text = "<b>📦 Ваши продукты:</b>\n\n"

    builder = InlineKeyboardBuilder()

    for row in rows:
        prod_id, name, added_date_str, expiry_date_str, interval = row
        if expiry_date_str:
            expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
            if expiry_date < today:
                status = "🔴 <b>ПРОСРОЧЕН</b>"
            else:
                status = f"годен до: {expiry_date_str}"
            text += f"• <b>{name}</b> — {status}\n"
        else:
            text += f"• <b>{name}</b> — без срока (напоминание каждые {interval} дн.)\n"

        # Кнопка удаления для каждого продукта
        builder.button(text=f"🗑 Удалить: {name}", callback_data=f"del_prod_{prod_id}")

    builder.adjust(1)  # Располагаем кнопки по одной в ряд
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@dp.message(F.text == "⚠️ Просроченные")
async def show_expired_products(message: types.Message):
    conn = sqlite3.connect("products_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, expiry_date FROM products WHERE user_id = ?", (message.from_user.id,))
    rows = cursor.fetchall()
    conn.close()

    today = datetime.now().date()
    expired = []

    for row in rows:
        prod_id, name, expiry_date_str = row
        if expiry_date_str:
            expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
            if expiry_date < today:
                expired.append((prod_id, name, expiry_date_str))

    if not expired:
        await message.answer("У вас нет просроченных продуктов! 🎉")
        return

    text = "<b>⚠️ Просроченные продукты:</b>\n\n"
    builder = InlineKeyboardBuilder()

    for prod_id, name, expiry_date_str in expired:
        text += f"• <b>{name}</b> (истёк {expiry_date_str})\n"
        builder.button(text=f"🗑 Удалить: {name}", callback_data=f"del_prod_{prod_id}")

    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("del_prod_"))
async def delete_product_callback(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[2])

    conn = sqlite3.connect("products_bot.db")
    cursor = conn.cursor()
    # Проверяем, принадлежит ли товар пользователю
    cursor.execute("SELECT name FROM products WHERE id = ? AND user_id = ?", (prod_id, callback.from_user.id))
    row = cursor.fetchone()

    if row:
        prod_name = row[0]
        cursor.execute("DELETE FROM products WHERE id = ?", (prod_id,))
        conn.commit()
        conn.close()

        # Редактируем сообщение, убирая клавиатуру и сообщая об успехе
        await callback.message.edit_text(f"✅ Продукт «{prod_name}» успешно удален из списка.")
    else:
        conn.close()
        await callback.answer("Продукт не найден или уже удален.", show_alert=True)


@dp.message(F.text == "🍳 Подобрать рецепт")
async def suggest_recipe(message: types.Message):
    conn = sqlite3.connect("products_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE user_id = ?", (message.from_user.id,))
    user_products = [row[0].lower() for row in cursor.fetchall()]

    cursor.execute("SELECT title, ingredients, instructions FROM recipes")
    recipes = cursor.fetchall()
    conn.close()

    if not user_products:
        await message.answer("Сначала добавьте продукты, чтобы я мог подобрать рецепт.")
        return

    best_recipe = None
    max_matches = 0

    for title, ingredients, instructions in recipes:
        ing_list = [i.strip().lower() for i in ingredients.split(",")]
        matches = sum(1 for ing in ing_list if any(ing in p for p in user_products))
        if matches > max_matches:
            max_matches = matches
            best_recipe = (title, ingredients, instructions)

    if best_recipe and max_matches > 0:
        title, ingredients, instructions = best_recipe
        await message.answer(
            f"🍳 **Рекомендую приготовить: {title}**\n\n"
            f"🛒 **Ингредиенты:** {ingredients}\n"
            f"📝 **Рецепт:** {instructions}",
            parse_mode="Markdown"
        )
    else:
        await message.answer("К сожалению, по вашим продуктам пока не нашлось подходящих рецептов в базе.")


# --- ФОНОВАЯ ПРОВЕРКА УВЕДОМЛЕНИЙ ---
async def check_expiring_products():
    conn = sqlite3.connect("products_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, name, added_date, expiry_date, reminder_interval, last_reminded FROM products")
    products = cursor.fetchall()

    now = datetime.now()
    today = now.date()

    for prod in products:
        prod_id, user_id, name, added_date_str, expiry_date_str, interval, last_reminded_str = prod
        added_date = datetime.strptime(added_date_str, "%Y-%m-%d")

        if expiry_date_str:
            expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
            total_life = (expiry_date - added_date).total_seconds()
            passed_life = (now - added_date).total_seconds()

            if total_life > 0:
                percentage = (passed_life / total_life) * 100
                # Если прошло >= 80% времени или срок уже вышел, а сегодня еще не напоминали
                if (percentage >= 80 or expiry_date.date() < today) and last_reminded_str != now.strftime("%Y-%m-%d"):
                    try:
                        status_text = "уже просрочен!" if expiry_date.date() < today else f"подходит к концу срок годности (годен до {expiry_date_str})"
                        await bot.send_message(
                            user_id,
                            f"⚠️ **Внимание!** Продукт **{name}** {status_text}. Рекомендуем использовать его или удалить."
                        )
                        cursor.execute("UPDATE products SET last_reminded = ? WHERE id = ?",
                                       (now.strftime("%Y-%m-%d"), prod_id))
                    except Exception as e:
                        logging.error(f"Failed to send reminder: {e}")
        elif interval:
            last_reminded = datetime.strptime(last_reminded_str, "%Y-%m-%d")
            if (now - last_reminded).days >= interval:
                try:
                    await bot.send_message(
                        user_id,
                        f"🔔 **Напоминание:** Как дела с продуктом **{name}**? Не забудьте проверить его статус!"
                    )
                    cursor.execute("UPDATE products SET last_reminded = ? WHERE id = ?",
                                   (now.strftime("%Y-%m-%d"), prod_id))
                except Exception as e:
                    logging.error(f"Failed to send periodic reminder: {e}")

    conn.commit()
    conn.close()


# --- ЗАПУСК БОТА ---
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_expiring_products, "cron", hour=9, minute=0)
    scheduler.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())