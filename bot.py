
import os
import logging
import sqlite3
from contextlib import closing

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
DB_PATH = os.getenv("DB_PATH", "lostfound.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

(
    TITLE,
    DESCRIPTION,
    LOCATION,
    DATE,
    PHOTO,
    CUSTODY,
    CONTACT,
) = range(7)

CLAIM_PREFIX = "claim_"
CAPTION_LIMIT = 1024


# ---------------------------------------------------------------------------
# Storage
#
# Only FOUND items are stored: we need to remember the private details
# (location, finder's contact) that are hidden from the public channel post
# and revealed only to someone who presses "It's mine".
# ---------------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS found_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finder_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                location TEXT NOT NULL,
                item_date TEXT NOT NULL,
                keeps_item INTEGER NOT NULL,
                contact TEXT,
                channel_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL REFERENCES found_items(id),
                claimer_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (item_id, claimer_id)
            );
            """
        )


def save_found_item(finder_id, data):
    with closing(db()) as conn, conn:
        cur = conn.execute(
            """
            INSERT INTO found_items
                (finder_id, title, description, location, item_date,
                 keeps_item, contact)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finder_id,
                data["title"],
                data["description"],
                data["location"],
                data["date"],
                1 if data.get("keeps_item") else 0,
                data.get("contact"),
            ),
        )
        return cur.lastrowid


def set_channel_message_id(item_id, message_id):
    with closing(db()) as conn, conn:
        conn.execute(
            "UPDATE found_items SET channel_message_id = ? WHERE id = ?",
            (message_id, item_id),
        )


def delete_found_item(item_id):
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM found_items WHERE id = ?", (item_id,))


def get_found_item(item_id):
    with closing(db()) as conn:
        return conn.execute(
            "SELECT * FROM found_items WHERE id = ?", (item_id,)
        ).fetchone()


def record_claim(item_id, claimer_id):
    """Returns True if this is the first time this user claims this item."""
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO claims (item_id, claimer_id) VALUES (?, ?)",
            (item_id, claimer_id),
        )
        return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔎 Report Lost", callback_data="report_lost"
            ),
            InlineKeyboardButton(
                "🎒 Report Found", callback_data="report_found"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔍 Search Announcements", callback_data="search"
            ),
            InlineKeyboardButton(
                "ℹ️ Help", callback_data="help"
            ),
        ],
    ])


def custody_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🤲 I'll keep it with me", callback_data="custody_keep"
            )
        ],
        [
            InlineKeyboardButton(
                "📍 I left it where I found it",
                callback_data="custody_leave",
            )
        ],
    ])


def keep_warning_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ OK, share my contact with the owner",
                callback_data="custody_keep_ok",
            )
        ],
        [
            InlineKeyboardButton("↩️ Go back", callback_data="custody_back")
        ],
    ])


def share_phone_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Share my phone number", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def claim_button(bot_username, item_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🙋 It's mine",
                url=f"https://t.me/{bot_username}?start={CLAIM_PREFIX}{item_id}",
            )
        ]
    ])


def step(context, number):
    total = 6 if context.user_data.get("kind") == "FOUND" else 5
    return f"Step {number} of {total}"


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Deep link from the "It's mine" button: /start claim_<id>
    if context.args and context.args[0].startswith(CLAIM_PREFIX):
        await handle_claim(update, context, context.args[0][len(CLAIM_PREFIX):])
        return

    await update.message.reply_text(
        "Welcome to Lost&Found! 🎒\n\n"
        "Report an item you lost or found on campus, "
        "or search for an existing announcement.",
        reply_markup=main_menu(),
    )


async def menu_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.data == "help":
        await query.edit_message_text(
            "How to use Lost&Found\n\n"
            "• Report Lost: describe an item you lost.\n"
            "• Report Found: describe an item you found.\n"
            "• It's mine: press it under a #FOUND post to see where "
            "the item is.\n"
            "• Search: learn how to find announcements.\n\n"
            "Do not publish passwords, bank card numbers, "
            "or other sensitive personal information.",
            reply_markup=main_menu(),
        )

    elif query.data == "search":
        await query.edit_message_text(
            "Search announcements in our channel using "
            "#LOST or #FOUND and the item's name.\n\n"
            "For example: #FOUND keys\n\n"
            "The full search feature will be added next.",
            reply_markup=main_menu(),
        )


# ---------------------------------------------------------------------------
# Report conversation
# ---------------------------------------------------------------------------

async def begin_report(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    context.user_data["kind"] = (
        "LOST" if query.data == "report_lost" else "FOUND"
    )

    await query.edit_message_text(
        f"Let's create your #{context.user_data['kind']} report.\n\n"
        f"{step(context, 1)}: Enter a short item title.\n"
        "Example: Black water bottle"
    )
    return TITLE


async def get_title(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    title = update.message.text.strip()

    if not title or len(title) > 100:
        await update.message.reply_text(
            "Please enter a title of 1–100 characters."
        )
        return TITLE

    context.user_data["title"] = title

    hint = "Do not include passwords or full bank card details."
    if context.user_data["kind"] == "FOUND":
        hint += (
            "\nDon't say where the item is now — the location is shown "
            "only to the person who claims it."
        )

    await update.message.reply_text(
        f"{step(context, 2)}: Describe the item.\n{hint}"
    )
    return DESCRIPTION


async def get_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    description = update.message.text.strip()

    if not description or len(description) > 1000:
        await update.message.reply_text(
            "Please enter a description of up to 1000 characters."
        )
        return DESCRIPTION

    context.user_data["description"] = description

    if context.user_data["kind"] == "FOUND":
        prompt = (
            f"{step(context, 3)}: Where did you find the item?\n"
            "Example: Main library, second floor\n\n"
            "🔒 This is not posted in the channel. It's only shown to "
            "the person who presses \"It's mine\"."
        )
    else:
        prompt = (
            f"{step(context, 3)}: Where did you lose the item?\n"
            "Example: Main library, second floor"
        )

    await update.message.reply_text(prompt)
    return LOCATION


async def get_location(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    location = update.message.text.strip()

    if not location or len(location) > 200:
        await update.message.reply_text(
            "Please enter a location of up to 200 characters."
        )
        return LOCATION

    context.user_data["location"] = location
    await update.message.reply_text(
        f"{step(context, 4)}: Enter the date the item was lost or found.\n"
        "Example: 2026-10-23"
    )
    return DATE


async def get_date(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    date = update.message.text.strip()

    if not date or len(date) > 50:
        await update.message.reply_text(
            "Please enter a valid date or a short date description."
        )
        return DATE

    context.user_data["date"] = date
    await update.message.reply_text(
        f"{step(context, 5)}: Send a photo of the item, or type /skip "
        "if you don't have one."
    )
    return PHOTO


async def after_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if context.user_data["kind"] == "FOUND":
        await update.effective_message.reply_text(
            f"{step(context, 6)}: What did you do with the item?",
            reply_markup=custody_keyboard(),
        )
        return CUSTODY

    return await publish_report(update, context)


async def get_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not update.message.photo:
        await update.message.reply_text(
            "Please send a photo, or type /skip."
        )
        return PHOTO

    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    return await after_photo(update, context)


async def skip_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["photo_file_id"] = None
    return await after_photo(update, context)


async def custody_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if query.data == "custody_back":
        await query.edit_message_text(
            f"{step(context, 6)}: What did you do with the item?",
            reply_markup=custody_keyboard(),
        )
        return CUSTODY

    if query.data == "custody_leave":
        context.user_data["keeps_item"] = False
        context.user_data["contact"] = None
        await query.edit_message_text(
            "📍 You left the item where you found it.\n"
            "Only the person who claims it will see the location."
        )
        return await publish_report(update, context)

    if query.data == "custody_keep":
        handle = f"@{user.username}" if user.username else "your contact"
        await query.edit_message_text(
            "⚠️ Please read before continuing\n\n"
            f"If you keep the item, {handle} will be sent to anyone who "
            "presses \"It's mine\" on your post, together with where you "
            "found it, so they can reach you to get it back.\n\n"
            "It will NOT be shown publicly in the channel.\n\n"
            "Tip: ask the person to describe the item before you hand "
            "it over.",
            reply_markup=keep_warning_keyboard(),
        )
        return CUSTODY

    # custody_keep_ok
    context.user_data["keeps_item"] = True

    if user.username:
        context.user_data["contact"] = f"@{user.username}"
        await query.edit_message_text(
            f"🤲 You're keeping the item. The owner will get your "
            f"contact: @{user.username}"
        )
        return await publish_report(update, context)

    await query.edit_message_text(
        "🤲 You're keeping the item.\n\n"
        "You don't have a Telegram username, so the owner can't message "
        "you directly. Tap the button below to share your phone number, "
        "or type another way to contact you (max 100 characters)."
    )
    await update.effective_message.reply_text(
        "How can the owner contact you?",
        reply_markup=share_phone_keyboard(),
    )
    return CONTACT


async def custody_reminder(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Please choose one of the options above, or type /cancel."
    )
    return CUSTODY


async def get_contact(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    message = update.message

    if message.contact:
        contact = message.contact.phone_number
        if not contact.startswith("+"):
            contact = f"+{contact}"
    else:
        contact = (message.text or "").strip()
        if not contact or len(contact) > 100:
            await message.reply_text(
                "Please send a contact of up to 100 characters, "
                "or tap the button to share your phone number."
            )
            return CONTACT

    context.user_data["contact"] = contact
    await message.reply_text(
        f"Got it. The owner will receive: {contact}",
        reply_markup=ReplyKeyboardRemove(),
    )
    return await publish_report(update, context)


async def send_announcement(bot, text, photo_file_id, reply_markup=None):
    """Posts to the channel and returns the message that carries the button."""
    if photo_file_id and len(text) <= CAPTION_LIMIT:
        return await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=photo_file_id,
            caption=text,
            reply_markup=reply_markup,
        )

    if photo_file_id:
        await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_file_id)

    return await bot.send_message(
        chat_id=CHANNEL_ID,
        text=text,
        reply_markup=reply_markup,
    )


async def publish_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    data = context.user_data
    kind = data["kind"]
    photo_file_id = data.get("photo_file_id")
    item_id = None

    try:
        if kind == "FOUND":
            # Location and finder's contact stay private.
            announcement = (
                "#FOUND\n\n"
                f"📌 Item: {data['title']}\n"
                f"📝 Description: {data['description']}\n"
                f"📅 Date: {data['date']}\n\n"
                "🔒 The item's location is shared only with its owner. "
                "If this is yours, press \"It's mine\" below."
            )
            item_id = save_found_item(update.effective_user.id, data)
            sent = await send_announcement(
                context.bot,
                announcement,
                photo_file_id,
                claim_button(context.bot.username, item_id),
            )
            set_channel_message_id(item_id, sent.message_id)
            done_text = (
                "✅ Your announcement was published!\n\n"
                f"Item: {data['title']}\n\n"
                "The location"
                + (" and your contact" if data.get("keeps_item") else "")
                + " will only be shown to people who press \"It's mine\". "
                "I'll message you here when someone does.\n\n"
                "Use /start to return to the main menu."
            )
        else:
            announcement = (
                f"#{kind}\n\n"
                f"📌 Item: {data['title']}\n"
                f"📝 Description: {data['description']}\n"
                f"📍 Location: {data['location']}\n"
                f"📅 Date: {data['date']}\n\n"
                "If you recognize this item, contact the reporter "
                "through the bot. Do not post private information publicly."
            )
            await send_announcement(context.bot, announcement, photo_file_id)
            done_text = (
                "✅ Your announcement was published!\n\n"
                f"Type: #{kind}\n"
                f"Item: {data['title']}\n\n"
                "Use /start to return to the main menu."
            )

        await update.effective_message.reply_text(done_text)

    except Exception:
        logger.exception("Could not publish announcement")
        if item_id is not None:
            delete_found_item(item_id)
        await update.effective_message.reply_text(
            "Sorry, I couldn't publish your announcement. "
            "Please try again later or contact an administrator."
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.clear()
    await update.message.reply_text(
        "Report cancelled.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        "Use the menu below to start again.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# "It's mine" claims
# ---------------------------------------------------------------------------

async def handle_claim(
    update: Update, context: ContextTypes.DEFAULT_TYPE, raw_item_id: str
):
    claimer = update.effective_user
    item = get_found_item(int(raw_item_id)) if raw_item_id.isdigit() else None

    if item is None:
        await update.message.reply_text(
            "Sorry, this item could not be found. It may have been removed.",
            reply_markup=main_menu(),
        )
        return

    if item["finder_id"] == claimer.id:
        await update.message.reply_text(
            "This is your own #FOUND post. When the owner presses "
            "\"It's mine\", I'll let you know here."
        )
        return

    text = (
        f"🙋 You claimed: {item['title']}\n"
        f"📝 {item['description']}\n"
        f"📅 Found on: {item['item_date']}\n"
        f"📍 Found at: {item['location']}\n\n"
    )
    if item["keeps_item"]:
        text += (
            "🤲 The finder is keeping the item. Contact them to "
            f"arrange the return: {item['contact']}\n\n"
            "Be ready to describe the item to prove it's yours."
        )
    else:
        text += (
            "📍 The finder left the item where they found it. "
            "Please go and check soon — it may have been moved."
        )
    await update.message.reply_text(text)

    if not record_claim(item["id"], claimer.id):
        return  # already notified the finder about this person

    claimer_name = claimer.full_name
    if claimer.username:
        claimer_name += f" (@{claimer.username})"

    notice = (
        f"🙋 {claimer_name} pressed \"It's mine\" on your #FOUND post: "
        f"{item['title']}\n\n"
    )
    if item["keeps_item"]:
        notice += (
            "They received your contact and may message you. "
            "Ask them to describe the item before handing it over."
        )
    else:
        notice += "They were told where you left the item."

    try:
        await context.bot.send_message(chat_id=item["finder_id"], text=notice)
    except Exception:
        logger.exception("Could not notify finder of item %s", item["id"])


# ---------------------------------------------------------------------------
# Misc commands
# ---------------------------------------------------------------------------

async def myid(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        f"Your Telegram user ID is: {update.effective_user.id}"
    )


async def testpost(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if (
        not ADMIN_USER_ID
        or str(update.effective_user.id) != ADMIN_USER_ID
    ):
        await update.message.reply_text(
            "Sorry, this command is for the bot administrator only."
        )
        return

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=(
                "🧪 TEST ANNOUNCEMENT\n\n"
                "#FOUND\n"
                "Item: Example keys\n"
                "Location: University library\n"
                "This is a test post."
            ),
        )
        await update.message.reply_text("Test announcement published!")
    except Exception:
        logger.exception("Test announcement failed")
        await update.message.reply_text(
            "Posting failed. Check the channel ID and bot permissions."
        )


def build_application(token=BOT_TOKEN, **builder_options):
    builder = Application.builder().token(token)
    for name, value in builder_options.items():
        getattr(builder, name)(value)
    app = builder.build()

    report_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                begin_report,
                pattern=r"^report_(lost|found)$",
            )
        ],
        states={
            TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)
            ],
            DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, get_description
                )
            ],
            LOCATION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, get_location
                )
            ],
            DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, get_date
                )
            ],
            PHOTO: [
                MessageHandler(filters.PHOTO, get_photo),
                CommandHandler("skip", skip_photo),
            ],
            CUSTODY: [
                CallbackQueryHandler(
                    custody_choice,
                    pattern=r"^custody_(keep|keep_ok|leave|back)$",
                ),
                MessageHandler(~filters.COMMAND, custody_reminder),
            ],
            CONTACT: [
                MessageHandler(
                    filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
                    get_contact,
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(report_conversation)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("testpost", testpost))
    app.add_handler(
        CallbackQueryHandler(menu_button, pattern=r"^(search|help)$")
    )
    return app


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "BOT_TOKEN or CHANNEL_ID is missing from .env"
        )

    init_db()
    app = build_application()

    print("Lost&Found bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
