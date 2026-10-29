import logging
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import TelegramError, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

DATABASE_FILE = "lost_found.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing from your .env file.")

if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID is missing from your .env file.")

if ADMIN_USER_ID:
    ADMIN_USER_ID = int(ADMIN_USER_ID)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Location and handoff options
# ---------------------------------------------------------

OTHER_LOCATION = "other:"

FOUND_LOCATIONS = [
    "Library",
    "Conference hall",
    "MATH",
    "UCA",
    "cafeteria",
    "MATH (hallway)",
    "Table tennis area",
    "Football court",
    "University yard",
    OTHER_LOCATION,
]

HANDOFF_OPTIONS = [
    "Left it where I found it.",
    "I took it, the owner contact with me",
    "Student affairs",
    "Entrance guards/security",
    "Library staff",
    "Other location or person (I'll write manually):",
]

OTHER_HANDOFF = "Other location or person (I'll write manually):"


# ---------------------------------------------------------
# Database
# ---------------------------------------------------------

def get_db():
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection



def init_db():
    """Create the database and add columns needed by this version of the bot.

    Existing databases are kept in place. In particular, older databases may
    have a required `kind` column, so publishing code also writes that field.
    """
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                description TEXT NOT NULL,
                photo_file_id TEXT,
                found_place TEXT NOT NULL,
                found_detail TEXT NOT NULL,
                handoff TEXT NOT NULL,
                handoff_detail TEXT,
                contact_type TEXT NOT NULL,
                contact_value TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'publishing',
                channel_message_id INTEGER,
                kind TEXT NOT NULL DEFAULT 'FOUND'
            )
        """)

        existing_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(reports)").fetchall()
        }

        # Defaults are supplied for columns that must be added to an older DB.
        migrations = {
            "user_id": "INTEGER",
            "username": "TEXT",
            "description": "TEXT",
            "photo_file_id": "TEXT",
            "found_place": "TEXT",
            "found_detail": "TEXT",
            "handoff": "TEXT",
            "handoff_detail": "TEXT",
            "contact_type": "TEXT",
            "contact_value": "TEXT",
            "created_at": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'publishing'",
            "channel_message_id": "INTEGER",
            "kind": "TEXT NOT NULL DEFAULT 'FOUND'",
        }

        for column, definition in migrations.items():
            if column not in existing_columns:
                db.execute(
                    f"ALTER TABLE reports ADD COLUMN {column} {definition}"
                )

        db.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                claimant_id INTEGER NOT NULL,
                claimant_username TEXT,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                claim_type TEXT NOT NULL DEFAULT 'contact_poster',
                status TEXT NOT NULL DEFAULT 'pending',
                poster_message_id INTEGER,
                FOREIGN KEY (report_id) REFERENCES reports(id)
            )
        """)

        # Safely extend claims tables created by earlier bot versions.
        claim_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(claims)").fetchall()
        }
        claim_migrations = {
            "claim_type": "TEXT NOT NULL DEFAULT 'contact_poster'",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "poster_message_id": "INTEGER",
        }
        for column, definition in claim_migrations.items():
            if column not in claim_columns:
                db.execute(f"ALTER TABLE claims ADD COLUMN {column} {definition}")


# ---------------------------------------------------------
# Keyboards
# ---------------------------------------------------------


FOUND_BUTTON = "🕵🏻 FOUND"
LOST_BUTTON = "🔎 LOST"


def main_keyboard():
    """Persistent reply keyboard shown after the user presses Start."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(FOUND_BUTTON), KeyboardButton(LOST_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Choose FOUND or LOST",
    )



def photo_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Skip photo",
                    callback_data="skip_photo",
                )
            ],
            [
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="cancel_flow",
                )
            ],
        ]
    )


def location_keyboard():
    buttons = [
        InlineKeyboardButton(
            "other (I'll write manually):" if location == OTHER_LOCATION else location,
            callback_data=f"location:{index}",
        )
        for index, location in enumerate(FOUND_LOCATIONS)
    ]

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append(
        [InlineKeyboardButton("Cancel", callback_data="cancel_flow")]
    )
    return InlineKeyboardMarkup(rows)


def handoff_keyboard():
    buttons = [
        InlineKeyboardButton(
            option,
            callback_data=f"handoff:{index}",
        )
        for index, option in enumerate(HANDOFF_OPTIONS)
    ]

    rows = [[button] for button in buttons]
    rows.append(
        [InlineKeyboardButton("Cancel", callback_data="cancel_flow")]
    )
    return InlineKeyboardMarkup(rows)


def contact_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Use my Telegram username",
                    callback_data="contact:telegram",
                )
            ],
            [
                InlineKeyboardButton(
                    "Use a phone number",
                    callback_data="contact:phone",
                )
            ],
            [
                InlineKeyboardButton(
                    "Contact me through the bot",
                    callback_data="contact:bot",
                )
            ],
            [
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="cancel_flow",
                )
            ],
        ]
    )


def contact_fallback_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Enter a phone number",
                    callback_data="contact:phone",
                )
            ],
            [
                InlineKeyboardButton(
                    "Contact me through the bot",
                    callback_data="contact:bot",
                )
            ],
            [
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="cancel_flow",
                )
            ],
        ]
    )


def preview_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Publish announcement",
                    callback_data="publish_found",
                )
            ],
            [
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="cancel_flow",
                )
            ],
        ]
    )


def claim_keyboard(report_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "I think this is mine",
                    callback_data=f"claim:{report_id}",
                )
            ]
        ]
    )


def lost_response_keyboard(report_id):
    """Button for someone who believes they found a LOST item."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "I found this item",
            callback_data=f"lost_response:{report_id}",
        )]]
    )



def claim_choice_keyboard(report_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(
                "I got my lost item",
                callback_data=f"claim_choice:{report_id}:got_item",
            )],
            [InlineKeyboardButton(
                "I wanna contact the person who posted this",
                callback_data=f"claim_choice:{report_id}:contact_poster",
            )],
        ]
    )


def claim_decision_keyboard(claim_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Approve and share my contact",
                    callback_data=f"claim_decision:{claim_id}:approve",
                )
            ],
            [
                InlineKeyboardButton(
                    "Decline contact sharing",
                    callback_data=f"claim_decision:{claim_id}:decline",
                )
            ],
        ]
    )


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def get_flow(context):
    return context.user_data.get("found_flow")


def format_found_place(flow):
    selected = flow["found_place"]
    detail = flow["found_detail"].strip()

    # Do not display the word "Other" in the announcement.
    if selected == OTHER_LOCATION:
        return detail

    # Avoid output such as "MATH — MATH 206".
    if detail.lower().startswith(selected.lower()):
        return detail

    return f"{selected} — {detail}"


def format_handoff(flow):
    selected = flow["handoff"]
    detail = (flow.get("handoff_detail") or "").strip()

    # Show only the manually entered details for "Other".
    if selected == OTHER_HANDOFF:
        return detail

    if detail:
        return f"{selected} — {detail}"

    return selected


def format_contact(flow):
    contact_type = flow["contact_type"]
    contact_value = flow.get("contact_value")

    if contact_type in {"telegram", "phone"}:
        return contact_value or "Contact details were not provided."

    if flow.get("kind", "FOUND") == "LOST":
        return "Use the “I found this item” button below."
    return "Use the “I think this is mine” button below."


def make_announcement(flow, report_id=None):
    photo_line = "📷 Photo: Attached\n" if flow.get("photo_file_id") else ""
    contact = format_contact(flow)
    place = format_found_place(flow)
    kind = flow.get("kind", "FOUND").upper()
    reported = datetime.now().strftime('%Y-%m-%d %H:%M')

    if kind == "LOST":
        return (
            "#LOST\n\n"
            f"🔎 Item: {flow['description'].strip()}\n"
            f"{photo_line}"
            f"📍 Probably lost at: {place}\n"
            f"📞 Contact: {contact}\n"
            f"📅 Reported: {reported}"
        )

    handoff = format_handoff(flow)
    return (
        "#FOUND\n\n"
        f"🔎 Item: {flow['description'].strip()}\n"
        f"{photo_line}"
        f"📍 Found at: {place}\n"
        f"📦 Left/handed in: {handoff}\n"
        f"📞 Contact: {contact}\n"
        f"📅 Reported: {reported}"
    )


async def prompt_description(chat_id, context):
    flow = get_flow(context)
    if not flow:
        return

    flow["step"] = "description"

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "Step 2 of 6\n\n"
            "What did you find left? Give short description"
        ),
    )


async def prompt_location(chat_id, context):
    flow = get_flow(context)
    if not flow:
        return

    flow["step"] = "location"
    if flow.get("kind", "FOUND") == "LOST":
        text = "Step 2 of 4\n\nWhere do you think you lost it? Choose the most likely place:"
    else:
        text = "Step 3 of 6\n\nWhere did you find it? Choose the place:"

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=location_keyboard(),
    )


async def prompt_lost_photo(chat_id, context):
    flow = get_flow(context)
    if not flow or flow.get("kind") != "LOST":
        return

    flow["step"] = "photo"
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "Step 3 of 4\n\n"
            "If possible, attach a photo of the lost item, or skip this step."
        ),
        reply_markup=photo_keyboard(),
    )


async def prompt_handoff(chat_id, context):
    flow = get_flow(context)
    if not flow:
        return

    flow["step"] = "handoff"

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "Step 4 of 6\n\n"
            "Did you leave it somewhere or hand in the item?\n"
            "Choose the option that best describes what happened:"
        ),
        reply_markup=handoff_keyboard(),
    )


async def prompt_contact(chat_id, context):
    flow = get_flow(context)
    if not flow:
        return

    flow["step"] = "contact"
    if flow.get("kind", "FOUND") == "LOST":
        text = (
            "Step 4 of 4\n\n"
            "If someone finds your item, how should they contact you?\n"
            "Your Telegram username will be filled in automatically if you have one."
        )
    else:
        text = (
            "Step 5 of 6\n\n"
            "How should the owner contact you?\n"
            "Your Telegram username will be filled in automatically if you have one."
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=contact_keyboard(),
    )


async def show_preview(chat_id, context):
    flow = get_flow(context)
    if not flow:
        return

    flow["step"] = "preview"
    if flow.get("kind", "FOUND") == "LOST":
        step_title = "Step 4 of 4 — Preview your LOST announcement"
    else:
        step_title = "Step 6 of 6 — Preview your FOUND announcement"

    text = (
        f"{step_title}\n\n"
        "Please check the details before publishing.\n\n"
        f"{make_announcement(flow)}"
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=preview_keyboard(),
    )


async def safe_send_message(context, chat_id, text):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except TelegramError:
        logger.warning("Could not send a message to chat %s", chat_id)


# ---------------------------------------------------------
# Start and cancel commands
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Do not bring the menu back in the middle of an active report.
    if get_flow(context) or context.user_data.get("lost_flow_pending"):
        await update.effective_message.reply_text(
            "Your reporting session is still active. Finish the steps or use /cancel "
            "to cancel and bring the FOUND / LOST buttons back."
        )
        return

    await update.effective_message.reply_text(
        "Welcome to the University Lost & Found bot.\n\n"
        "Choose FOUND to report an item you found, or LOST to start a "
        "lost-item report.",
        reply_markup=main_keyboard(),
    )



async def start_found(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    context.user_data.pop("lost_flow_pending", None)
    context.user_data["found_flow"] = {
        "kind": "FOUND",
        "step": "photo",
        "user_id": user.id,
        "username": f"@{user.username}" if user.username else None,
        "photo_file_id": None,
        "description": "",
        "found_place": "",
        "found_detail": "",
        "handoff": "",
        "handoff_detail": "",
        "contact_type": "",
        "contact_value": None,
    }

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except TelegramError:
            pass

    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text="Starting your FOUND report. The menu will return when you publish or cancel.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "Step 1 of 6\n\n"
            "Would you like to attach a photo of the item?\n"
            "Send a photo or press “Skip photo”."
        ),
        reply_markup=photo_keyboard(),
    )


async def start_lost(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Start the four-step LOST report flow."""
    user = update.effective_user
    context.user_data.pop("lost_flow_pending", None)
    context.user_data["found_flow"] = {
        "kind": "LOST",
        "step": "description",
        "user_id": user.id,
        "username": f"@{user.username}" if user.username else None,
        "photo_file_id": None,
        "description": "",
        "found_place": "",
        "found_detail": "",
        "handoff": "",
        "handoff_detail": "",
        "contact_type": "",
        "contact_value": None,
    }

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except TelegramError:
            pass

    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text="Starting your LOST report. The menu will return when you publish or cancel.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Step 1 of 4\n\nPlease describe the item you lost.",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("found_flow", None)
    context.user_data.pop("claim_waiting", None)
    context.user_data.pop("lost_flow_pending", None)

    await update.effective_message.reply_text(
        "Cancelled. Choose FOUND or LOST below to continue, or use /start.",
        reply_markup=main_keyboard(),
    )


# ---------------------------------------------------------
# Photo handling
# ---------------------------------------------------------

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    flow = get_flow(context)

    if not flow or flow.get("step") != "photo":
        await update.effective_message.reply_text(
            "To attach a photo to a new report, start with /found or /lost."
        )
        return

    photo = update.effective_message.photo[-1]
    flow["photo_file_id"] = photo.file_id

    await update.effective_message.reply_text("Photo attached successfully.")

    if flow.get("kind", "FOUND") == "LOST":
        await prompt_contact(update.effective_chat.id, context)
    else:
        await prompt_description(update.effective_chat.id, context)


# ---------------------------------------------------------
# Text handling
# ---------------------------------------------------------

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user
    text = (message.text or "").strip()

    # A claim's identifying details must be handled privately.
    if context.user_data.get("claim_waiting"):
        await receive_claim_detail(update, context)
        return

    # Handle menu buttons before interpreting text as a report answer.
    normalized_text = text.casefold()
    if normalized_text == FOUND_BUTTON.casefold() or normalized_text == "found":
        await start_found(update, context)
        return

    if normalized_text == LOST_BUTTON.casefold() or normalized_text == "lost":
        await start_lost(update, context)
        return

    flow = get_flow(context)

    if not flow:
        await message.reply_text(
            "Choose FOUND or LOST below, or use /start to show the menu.",
            reply_markup=main_keyboard(),
        )
        return

    step = flow.get("step")

    if step == "photo":
        await message.reply_text(
            "Please send a photo or press “Skip photo”.",
            reply_markup=photo_keyboard(),
        )
        return

    if step == "description":
        if not text:
            await message.reply_text(
                "Please enter a short description of the item."
            )
            return

        flow["description"] = text
        await prompt_location(update.effective_chat.id, context)
        return

    if step == "location_detail":
        if not text:
            await message.reply_text("Please specify the exact place.")
            return

        flow["found_detail"] = text
        if flow.get("kind", "FOUND") == "LOST":
            await prompt_lost_photo(update.effective_chat.id, context)
        else:
            await prompt_handoff(update.effective_chat.id, context)
        return

    if step == "handoff_detail":
        if not text:
            await message.reply_text(
                "Please enter the details of the place or person."
            )
            return

        flow["handoff_detail"] = text
        await prompt_contact(update.effective_chat.id, context)
        return

    if step == "phone":
        if len(text) < 5:
            await message.reply_text(
                "That number seems too short. Please enter a valid "
                "contact number, or use /cancel to stop."
            )
            return

        flow["contact_type"] = "phone"
        flow["contact_value"] = text
        await show_preview(update.effective_chat.id, context)
        return

    if step == "preview":
        await message.reply_text(
            "Your report is ready. Use the buttons in the preview "
            "to publish it or cancel."
        )
        return

    if step == "contact_fallback":
        await message.reply_text(
            "Please choose a contact option using the buttons below.",
            reply_markup=contact_fallback_keyboard(),
        )
        return

    await message.reply_text(
        "Please use the buttons or follow the latest instruction."
    )


# ---------------------------------------------------------
# Location and handoff selection
# ---------------------------------------------------------

async def choose_location(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    index: int,
):
    flow = get_flow(context)

    if not flow:
        await query.message.reply_text(
            "Your report session has expired. Please start again with /found or /lost."
        )
        return

    if index < 0 or index >= len(FOUND_LOCATIONS):
        await query.message.reply_text("Invalid location selection.")
        return

    selected = FOUND_LOCATIONS[index]
    flow["found_place"] = selected
    flow["step"] = "location_detail"

    if flow.get("kind", "FOUND") == "LOST":
        if selected == OTHER_LOCATION:
            prompt = "Where do you think you lost it? Please write the most likely exact place."
        else:
            prompt = (
                f"You chose {selected}.\n\n"
                "Please specify the most likely exact place where you lost it "
                "(for example, UCA 204 near the window)."
            )
    elif selected == OTHER_LOCATION:
        prompt = (
            "Please specify the exact place "
            "(for example, UCA 204 near last row tables or near window)."
        )
    else:
        prompt = (
            f"You chose {selected}.\n\n"
            "Please specify the exact place "
            "(for example, UCA 204 near last row tables or near window)."
        )

    await query.message.reply_text(prompt)


async def choose_handoff(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    index: int,
):
    flow = get_flow(context)

    if not flow:
        await query.message.reply_text(
            "Your report session has expired. Please start again with /found."
        )
        return

    if index < 0 or index >= len(HANDOFF_OPTIONS):
        await query.message.reply_text("Invalid selection.")
        return

    selected = HANDOFF_OPTIONS[index]
    flow["handoff"] = selected
    flow["handoff_detail"] = ""

    # These two choices do not need an additional explanation.
    if selected in (
        "Left it where I found it.",
        "I took it, the owner contact with me",
    ):
        await prompt_contact(query.message.chat_id, context)
        return

    flow["step"] = "handoff_detail"

    if selected == OTHER_HANDOFF:
        prompt = (
            "Please clearly write the details of place you left or "
            "person handed in (for example teacher assistant from "
            "Calculus [name surname] or near vending machine in UCA "
            "1st floor )."
        )
    else:
        prompt = (
            f"You selected {selected}.\n\n"
            "Please clearly write the details of place you left or "
            "person handed in (for example teacher assistant from "
            "Calculus [name surname] or near vending machine in UCA "
            "1st floor )."
        )

    await query.message.reply_text(prompt)


# ---------------------------------------------------------
# Contact selection
# ---------------------------------------------------------

async def choose_contact(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    choice: str,
):
    flow = get_flow(context)

    if not flow:
        await query.message.reply_text(
            "Your report session has expired. Please start again with /found."
        )
        return

    user = query.from_user

    if choice == "telegram":
        if user.username:
            # Use their actual Telegram username automatically.
            flow["contact_type"] = "telegram"
            flow["contact_value"] = f"@{user.username}"
            await show_preview(query.message.chat_id, context)
        else:
            flow["step"] = "contact_fallback"
            await query.message.reply_text(
                "You don't have a Telegram username set up, so I can't "
                "fill one in automatically.\n\n"
                "You can enter a phone number or let the owner contact "
                "you through the bot.",
                reply_markup=contact_fallback_keyboard(),
            )
        return

    if choice == "phone":
        flow["step"] = "phone"
        await query.message.reply_text(
            ("Please enter the phone number you want to share with the person who finds your item.\n\n"
             "Only enter a number you are comfortable publishing in the announcement.")
            if flow.get("kind", "FOUND") == "LOST"
            else ("Please enter the phone number you want to share with the owner.\n\n"
                  "Only enter a number you are comfortable publishing in the announcement.")
        )
        return

    if choice == "bot":
        flow["contact_type"] = "bot"
        flow["contact_value"] = None
        await show_preview(query.message.chat_id, context)
        return


# ---------------------------------------------------------
# Legacy database compatibility
# ---------------------------------------------------------

def insert_report_compatible(db, flow, user, created_at):
    """Insert a report while satisfying required columns in older DB schemas.

    Some older versions of this project used extra NOT NULL columns such as
    `kind`. This builds the INSERT from the actual table schema so those
    columns receive sensible values instead of breaking publication.
    """
    username = f"@{user.username}" if user.username else None
    found_place = flow.get("found_place") or ""
    found_detail = flow.get("found_detail") or ""
    handoff = flow.get("handoff") or ""
    handoff_detail = flow.get("handoff_detail") or None
    description = flow.get("description") or ""
    photo_file_id = flow.get("photo_file_id")
    contact_type = flow.get("contact_type") or "bot"
    contact_value = flow.get("contact_value")

    # Canonical fields plus common names used by earlier versions.
    values = {
        "user_id": user.id,
        "reporter_id": user.id,
        "owner_id": user.id,
        "created_by": user.id,
        "username": username,
        "reporter_username": username,
        "kind": flow.get("kind", "FOUND").upper(),
        "type": flow.get("kind", "FOUND").upper(),
        "report_type": flow.get("kind", "FOUND").upper(),
        "description": description,
        "title": description,
        "item_name": description,
        "item_description": description,
        "photo_file_id": photo_file_id,
        "photo": photo_file_id,
        "found_place": found_place,
        "location": found_place,
        "place": found_place,
        "found_detail": found_detail,
        "location_detail": found_detail,
        "details": description,
        "handoff": handoff,
        "handover": handoff,
        "handoff_detail": handoff_detail,
        "handoff_location": handoff_detail or handoff,
        "contact_type": contact_type,
        "contact_value": contact_value,
        "contact": contact_value or contact_type,
        "contact_info": contact_value or contact_type,
        "created_at": created_at,
        "created": created_at,
        "date": created_at,
        "status": "publishing",
        "channel_message_id": None,
    }

    columns_info = db.execute("PRAGMA table_info(reports)").fetchall()
    insert_columns = []
    insert_values = []

    for col in columns_info:
        name = col["name"]
        lower_name = name.lower()
        # Let SQLite generate an integer primary key.
        if col["pk"] and "INT" in (col["type"] or "").upper():
            continue

        if lower_name in values:
            value = values[lower_name]
            # If an older required field expects a value, avoid inserting NULL.
            if col["notnull"] and value is None and col["dflt_value"] is None:
                value = ""
            insert_columns.append(name)
            insert_values.append(value)
            continue

        # Omit unknown columns that have a database default or allow NULL.
        if not col["notnull"] or col["dflt_value"] is not None:
            continue

        # Satisfy any extra legacy NOT NULL columns not known to this version.
        # SQLite is dynamically typed; empty text is a safe generic fallback.
        if lower_name in {"kind", "type", "report_type"}:
            fallback = flow.get("kind", "FOUND").upper()
        elif "INT" in (col["type"] or "").upper():
            fallback = 0
        else:
            fallback = ""
        insert_columns.append(name)
        insert_values.append(fallback)

    if not insert_columns:
        raise sqlite3.OperationalError("The reports table has no insertable columns.")

    quoted_columns = ", ".join('"' + name.replace('"', '""') + '"' for name in insert_columns)
    placeholders = ", ".join("?" for _ in insert_columns)
    sql = f"INSERT INTO reports ({quoted_columns}) VALUES ({placeholders})"
    cursor = db.execute(sql, insert_values)
    return cursor.lastrowid


# ---------------------------------------------------------
# Publishing the announcement
# ---------------------------------------------------------

async def publish_found(
    query,
    context: ContextTypes.DEFAULT_TYPE,
):
    flow = get_flow(context)

    if not flow or flow.get("step") != "preview":
        await query.message.reply_text(
            "There is no ready-to-publish report. Please start again with /found."
        )
        return

    user = query.from_user
    created_at = datetime.now().isoformat(timespec="seconds")

    # Save the report first so the channel's claim button has a report ID.
    # This insert adapts to older versions of the reports table, including
    # legacy required columns such as kind/type.
    try:
        with get_db() as db:
            report_id = insert_report_compatible(db, flow, user, created_at)
    except sqlite3.Error as error:
        logger.exception("Database error while saving %s report", flow.get("kind", "FOUND"))
        await query.message.reply_text(
            "I couldn't save this report to the database, so it was not published. "
            "Your answers are still in this chat; please try Publish again.\n\n"
            f"Database error: {error}"
        )
        return

    announcement = make_announcement(flow, report_id)
    # Telegram text messages have a 4096-character limit. Keep a little room
    # for safety if someone enters unusually long manual details.
    if len(announcement) > 4000:
        announcement = announcement[:3980].rstrip() + "\n…(announcement shortened)"

    report_kind = flow.get("kind", "FOUND").upper()
    keyboard = lost_response_keyboard(report_id) if report_kind == "LOST" else claim_keyboard(report_id)
    channel_message_id = None

    try:
        photo_file_id = flow.get("photo_file_id")

        if photo_file_id and len(announcement) <= 1024:
            posted = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo_file_id,
                caption=announcement,
                reply_markup=keyboard,
            )
            channel_message_id = posted.message_id
        else:
            # Keep the complete announcement and claim button together when
            # the caption would exceed Telegram's photo-caption limit.
            if photo_file_id:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=photo_file_id,
                )

            posted = await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=announcement,
                reply_markup=keyboard,
            )
            channel_message_id = posted.message_id

    except TelegramError as error:
        logger.exception("Could not publish found-item announcement")
        try:
            with get_db() as db:
                db.execute(
                    "UPDATE reports SET status = ? WHERE id = ?",
                    ("failed", report_id),
                )
        except sqlite3.Error:
            logger.exception("Could not mark failed report in database")

        await query.message.reply_text(
            "I saved your report, but Telegram couldn't publish it to the channel. "
            "Please check that the bot is an administrator with permission to post. "
            "Your answers have been kept; you can try publishing again.\n\n"
            f"Telegram error: {error}"
        )
        return

    try:
        with get_db() as db:
            db.execute(
                "UPDATE reports SET status = ?, channel_message_id = ? WHERE id = ?",
                ("posted", channel_message_id, report_id),
            )
    except sqlite3.Error:
        # The post is already live, so do not tell the user publication failed.
        logger.exception(
            "Announcement was published, but its database status could not be updated"
        )

    context.user_data.pop("found_flow", None)

    await query.message.reply_text(
        f"Your {report_kind} announcement has been published successfully.\n\n"
        "Choose FOUND or LOST below if you want to make another report.",
        reply_markup=main_keyboard(),
    )

    if ADMIN_USER_ID:
        await safe_send_message(
            context,
            ADMIN_USER_ID,
            f"New #{report_kind} report published. Report ID: {report_id}",
        )


# ---------------------------------------------------------
# Private claim flow
# ---------------------------------------------------------

async def claim_item(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    report_id: int,
):
    claimant = query.from_user

    with get_db() as db:
        report = db.execute(
            "SELECT * FROM reports WHERE id = ? AND status = ?",
            (report_id, "posted"),
        ).fetchone()

    if not report:
        await query.answer(
            "This announcement is no longer available.",
            show_alert=True,
        )
        return

    if report["user_id"] == claimant.id:
        await query.answer("This is your own report.", show_alert=True)
        return

    # Avoid restarting a different in-progress claim silently.
    try:
        await context.bot.send_message(
            chat_id=claimant.id,
            text=(
                f"You are responding to found-item report #{report_id}.\n\n"
                f"Item: {report['description']}\n\n"
                "What would you like to do? Choose one option below. "
                "After that, I will ask you for details to help the poster "
                "verify the item. Your details are sent privately to the poster."
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
        await context.bot.send_message(
            chat_id=claimant.id,
            text="Choose an option:",
            reply_markup=claim_choice_keyboard(report_id),
        )
    except Forbidden:
        await query.answer(
            "Open this bot in a private chat, press Start, then click the claim button again.",
            show_alert=True,
        )
        return
    except TelegramError:
        logger.exception("Could not start private claim flow")
        await query.answer(
            "I couldn't message you privately. Open the bot and try again.",
            show_alert=True,
        )
        return

    await query.answer("Check your private chat with the bot.")


async def respond_to_lost_report(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    report_id: int,
):
    """Privately collect details from someone who found a reported LOST item."""
    finder = query.from_user
    with get_db() as db:
        report = db.execute(
            "SELECT * FROM reports WHERE id = ? AND status = ?",
            (report_id, "posted"),
        ).fetchone()

    if not report or str(report["kind"]).upper() != "LOST":
        await query.answer("This lost-item announcement is no longer available.", show_alert=True)
        return
    if report["user_id"] == finder.id:
        await query.answer("This is your own report.", show_alert=True)
        return

    try:
        await context.bot.send_message(
            chat_id=finder.id,
            text=(
                f"You are responding to LOST report #{report_id}.\n\n"
                f"Item: {report['description']}\n\n"
                "Please privately describe where you found the item and any details "
                "that can help identify it. Your message will be sent to the person "
                "who posted the LOST report. They must approve before their contact "
                "details are shared with you."
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
    except Forbidden:
        await query.answer(
            "Open this bot in a private chat, press Start, then press the button again.",
            show_alert=True,
        )
        return
    except TelegramError:
        logger.exception("Could not start lost-report response flow")
        await query.answer("I couldn't message you privately. Open the bot and try again.", show_alert=True)
        return

    context.user_data["claim_waiting"] = {
        "report_id": report_id,
        "claim_type": "found_item",
    }
    await query.answer("Check your private chat with the bot.")


async def choose_claim_type(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    report_id: int,
    claim_type: str,
):
    claimant = query.from_user
    if claim_type not in {"got_item", "contact_poster"}:
        await query.answer("Invalid option.", show_alert=True)
        return

    with get_db() as db:
        report = db.execute(
            "SELECT * FROM reports WHERE id = ? AND status = ?",
            (report_id, "posted"),
        ).fetchone()

    if not report:
        await query.answer("This announcement is no longer available.", show_alert=True)
        return

    if report["user_id"] == claimant.id:
        await query.answer("This is your own report.", show_alert=True)
        return

    context.user_data["claim_waiting"] = {
        "report_id": report_id,
        "claim_type": claim_type,
    }

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    if claim_type == "got_item":
        prompt = (
            f"Report #{report_id}: {report['description']}\n\n"
            "Please describe the item and any details that can help verify it "
            "belongs to you (for example, a mark, colour, contents, or other "
            "detail not mentioned in the announcement). The poster will review "
            "your message and decide whether to share their contact."
        )
    else:
        prompt = (
            f"Report #{report_id}: {report['description']}\n\n"
            "Please describe the item and include details that help the poster "
            "verify it belongs to you. The poster must approve before their "
            "contact details are shared with you."
        )

    await query.message.reply_text(prompt, reply_markup=ReplyKeyboardRemove())
    await query.answer()


def insert_claim_compatible(db, report_id, claimant, detail, created_at, claim_type):
    """Insert a claim while tolerating extra required columns in older DBs."""
    values = {
        "report_id": report_id,
        "claimant_id": claimant.id,
        "claimant_username": f"@{claimant.username}" if claimant.username else None,
        "detail": detail,
        "created_at": created_at,
        "claim_type": claim_type,
        "status": "pending",
        "poster_message_id": None,
    }
    info = db.execute("PRAGMA table_info(claims)").fetchall()
    columns, parameters = [], []
    for col in info:
        name = col["name"]
        if col["pk"] and "INT" in (col["type"] or "").upper():
            continue
        if name.lower() in values:
            value = values[name.lower()]
            if col["notnull"] and value is None and col["dflt_value"] is None:
                value = ""
            columns.append(name)
            parameters.append(value)
        elif col["notnull"] and col["dflt_value"] is None:
            if "INT" in (col["type"] or "").upper():
                columns.append(name)
                parameters.append(0)
            else:
                columns.append(name)
                parameters.append("")
    quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
    placeholders = ", ".join("?" for _ in columns)
    cursor = db.execute(
        f"INSERT INTO claims ({quoted}) VALUES ({placeholders})",
        parameters,
    )
    return cursor.lastrowid


async def receive_claim_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    claimant = update.effective_user
    claim_state = context.user_data.get("claim_waiting")
    if not claim_state:
        return

    # Support the old integer state if an older in-memory update is present.
    if isinstance(claim_state, dict):
        report_id = int(claim_state["report_id"])
        claim_type = claim_state.get("claim_type", "contact_poster")
    else:
        report_id = int(claim_state)
        claim_type = "contact_poster"

    detail = (message.text or "").strip()
    if not detail:
        await message.reply_text("Please send the item details as text.")
        return

    with get_db() as db:
        report = db.execute(
            "SELECT * FROM reports WHERE id = ? AND status = ?",
            (report_id, "posted"),
        ).fetchone()
        if not report:
            context.user_data.pop("claim_waiting", None)
            await message.reply_text(
                "Sorry, that announcement is no longer available.",
                reply_markup=main_keyboard(),
            )
            return

        claim_id = insert_claim_compatible(
            db,
            report_id,
            claimant,
            detail,
            datetime.now().isoformat(timespec="seconds"),
            claim_type,
        )

    context.user_data.pop("claim_waiting", None)
    await message.reply_text(
        "Thank you. Your details have been sent privately to the person who "
        "posted the report. They must approve before their contact is shared.\n\n"
        "If the item still cannot be returned or you cannot contact the poster, "
        "please contact the admin using the contact information in this bot's description.",
        reply_markup=main_keyboard(),
    )

    report_kind = str(report["kind"] if "kind" in report.keys() else "FOUND").upper()
    if report_kind == "LOST":
        choice_text = "This person says they found your lost item"
    elif claim_type == "got_item":
        choice_text = "I got my lost item"
    else:
        choice_text = "I wanna contact the person who posted this"

    poster_message = (
        f"A person responded to your #{report_kind} report #{report_id}.\n\n"
        f"Item: {report['description']}\n"
        f"Their response: {choice_text}\n\n"
        f"Responder: {('@' + claimant.username) if claimant.username else 'No Telegram username'}\n"
        f"Details they provided:\n{detail}\n\n"
        "Approve only if you are comfortable sharing your contact details. "
        "Your contact will not be shared unless you approve below."
    )

    try:
        sent = await context.bot.send_message(
            chat_id=report["user_id"],
            text=poster_message,
            reply_markup=claim_decision_keyboard(claim_id),
        )
        with get_db() as db:
            db.execute(
                "UPDATE claims SET poster_message_id = ? WHERE id = ?",
                (sent.message_id, claim_id),
            )
    except Forbidden:
        logger.warning("Cannot notify report poster %s about claim %s", report["user_id"], claim_id)
        with get_db() as db:
            db.execute("UPDATE claims SET status = ? WHERE id = ?", ("notification_failed", claim_id))
        await safe_send_message(
            context,
            claimant.id,
            "I saved your claim, but Telegram would not let the bot notify the poster. "
            "Please contact the admin using the contact information in this bot's description.",
        )
    except TelegramError:
        logger.exception("Failed to notify report poster about claim")
        with get_db() as db:
            db.execute("UPDATE claims SET status = ? WHERE id = ?", ("notification_failed", claim_id))
        await safe_send_message(
            context,
            claimant.id,
            "I saved your claim, but could not notify the poster. Please contact the admin "
            "using the contact information in this bot's description.",
        )

    if ADMIN_USER_ID:
        await safe_send_message(
            context,
            ADMIN_USER_ID,
            f"New claim #{claim_id} for report #{report_id}.\n"
            f"Claimant ID: {claimant.id}\nDetails: {detail}",
        )


async def decide_claim(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    claim_id: int,
    decision: str,
):
    if decision not in {"approve", "decline"}:
        await query.answer("Invalid decision.", show_alert=True)
        return

    with get_db() as db:
        claim = db.execute(
            "SELECT * FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()
        if not claim:
            await query.answer("This claim no longer exists.", show_alert=True)
            return

        report = db.execute(
            "SELECT * FROM reports WHERE id = ?",
            (claim["report_id"],),
        ).fetchone()
        if not report:
            await query.answer("The original report no longer exists.", show_alert=True)
            return

        if query.from_user.id != report["user_id"]:
            await query.answer("Only the person who posted this report can decide.", show_alert=True)
            return

        if claim["status"] != "pending":
            await query.answer("This claim has already been handled.", show_alert=True)
            return

        new_status = "approved" if decision == "approve" else "declined"
        db.execute("UPDATE claims SET status = ? WHERE id = ?", (new_status, claim_id))

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    if decision == "decline":
        await query.answer("Claim declined.")
        await query.message.reply_text(
            f"You declined contact sharing for claim #{claim_id}."
        )
        await safe_send_message(
            context,
            claim["claimant_id"],
            "The person who posted the report declined to share their contact details. "
            "If you still need help recovering the item, please contact the admin using "
            "the contact information in this bot's description.",
            )
        return

    # Explicit approval permits contact sharing in this private conversation.
    report_kind = str(report["kind"] if "kind" in report.keys() else "FOUND").upper()
    poster_username = report["username"] if "username" in report.keys() else None
    contact_type = report["contact_type"] if "contact_type" in report.keys() else None
    contact_value = report["contact_value"] if "contact_value" in report.keys() else None
    if contact_value:
        shared_contact = contact_value
        if contact_type == "phone":
            contact_label = f"Phone: {shared_contact}"
        else:
            contact_label = f"Contact: {shared_contact}"
    elif poster_username:
        shared_contact = poster_username
        contact_label = f"Telegram: {shared_contact}"
    else:
        contact_label = (
            "The poster approved, but no direct contact detail was saved in the report."
        )

    await query.answer("Contact sharing approved.")
    await query.message.reply_text(
        f"You approved contact sharing for claim #{claim_id}."
    )
    await safe_send_message(
        context,
        claim["claimant_id"],
        (
            f"Good news! The person who posted #{report_kind} report #{claim['report_id']} "
            "approved your request.\n\n"
            f"{contact_label}\n\n"
            "Please contact them to arrange returning the item. If the item still "
            "cannot be returned or you cannot reach the poster, contact the admin "
            "using the contact information in this bot's description."
        ),
    )


# ---------------------------------------------------------
# Callback router
# ---------------------------------------------------------

async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    data = query.data or ""

    if data == "start_found":
        await query.answer()
        await start_found(update, context)
        return

    if data == "cancel_flow":
        await query.answer()
        context.user_data.pop("found_flow", None)
        context.user_data.pop("lost_flow_pending", None)
        await query.message.reply_text(
            "Cancelled. Choose FOUND or LOST below to continue.",
            reply_markup=main_keyboard(),
        )
        return

    if data == "skip_photo":
        await query.answer()
        flow = get_flow(context)

        if not flow or flow.get("step") != "photo":
            await query.message.reply_text(
                "There is no active photo step. Start a report with /found or /lost."
            )
            return

        flow["photo_file_id"] = None
        if flow.get("kind", "FOUND") == "LOST":
            await prompt_contact(query.message.chat_id, context)
        else:
            await prompt_description(query.message.chat_id, context)
        return

    if data.startswith("location:"):
        await query.answer()

        try:
            index = int(data.split(":", 1)[1])
        except ValueError:
            await query.message.reply_text("Invalid location selection.")
            return

        await choose_location(query, context, index)
        return

    if data.startswith("handoff:"):
        await query.answer()

        try:
            index = int(data.split(":", 1)[1])
        except ValueError:
            await query.message.reply_text("Invalid selection.")
            return

        await choose_handoff(query, context, index)
        return

    if data.startswith("contact:"):
        await query.answer()
        choice = data.split(":", 1)[1]
        await choose_contact(query, context, choice)
        return

    if data == "publish_found":
        await query.answer()
        await publish_found(query, context)
        return

    if data.startswith("claim_choice:"):
        try:
            _, report_id_text, claim_type = data.split(":", 2)
            report_id = int(report_id_text)
        except (ValueError, TypeError):
            await query.answer("Invalid claim option.", show_alert=True)
            return
        await choose_claim_type(query, context, report_id, claim_type)
        return

    if data.startswith("claim_decision:"):
        try:
            _, claim_id_text, decision = data.split(":", 2)
            claim_id = int(claim_id_text)
        except (ValueError, TypeError):
            await query.answer("Invalid decision.", show_alert=True)
            return
        await decide_claim(query, context, claim_id, decision)
        return

    if data.startswith("lost_response:"):
        try:
            report_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Invalid report.", show_alert=True)
            return
        await respond_to_lost_report(query, context, report_id)
        return

    if data.startswith("claim:"):
        try:
            report_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Invalid report.", show_alert=True)
            return

        await claim_item(query, context, report_id)
        return

    await query.answer("Unknown button.")


# ---------------------------------------------------------
# Error handling
# ---------------------------------------------------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "An error occurred while handling an update:",
        exc_info=context.error,
    )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("found", start_found))
    app.add_handler(CommandHandler("lost", start_lost))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    app.add_error_handler(error_handler)

    print("Lost & Found bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
