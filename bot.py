
import os
import logging

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TITLE, DESCRIPTION, LOCATION, DATE, PHOTO = range(5)


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "Step 1 of 5: Enter a short item title.\n"
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
    await update.message.reply_text(
        "Step 2 of 5: Describe the item.\n"
        "Do not include passwords or full bank card details."
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
    await update.message.reply_text(
        "Step 3 of 5: Where was the item lost or found?\n"
        "Example: Main library, second floor"
    )
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
        "Step 4 of 5: Enter the date the item was lost or found.\n"
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
        "Step 5 of 5: Send a photo of the item, or type /skip "
        "if you don't have one."
    )
    return PHOTO


async def publish_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    photo_file_id=None,
):
    data = context.user_data
    kind = data["kind"]

    announcement = (
        f"#{kind}\n\n"
        f"📌 Item: {data['title']}\n"
        f"📝 Description: {data['description']}\n"
        f"📍 Location: {data['location']}\n"
        f"📅 Date: {data['date']}\n\n"
        "If you recognize this item, contact the reporter "
        "through the bot. Do not post private information publicly."
    )

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=announcement,
        )

        if photo_file_id:
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo_file_id,
                caption=f"Photo for #{kind}: {data['title']}",
            )

        await update.effective_message.reply_text(
            "✅ Your announcement was published!\n\n"
            f"Type: #{kind}\n"
            f"Item: {data['title']}\n\n"
            "Use /start to return to the main menu."
        )

    except Exception:
        logger.exception("Could not publish announcement")
        await update.effective_message.reply_text(
            "Sorry, I couldn't publish your announcement. "
            "Please try again later or contact an administrator."
        )

    context.user_data.clear()
    return ConversationHandler.END


async def get_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not update.message.photo:
        await update.message.reply_text(
            "Please send a photo, or type /skip."
        )
        return PHOTO

    photo_file_id = update.message.photo[-1].file_id
    return await publish_report(update, context, photo_file_id)


async def skip_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    return await publish_report(update, context)


async def cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.clear()
    await update.message.reply_text(
        "Report cancelled. Use /start to open the menu.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


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


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "BOT_TOKEN or CHANNEL_ID is missing from .env"
        )

    app = Application.builder().token(BOT_TOKEN).build()

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

    print("Lost&Found bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
