"""University Lost & Found Telegram bot.

Users report FOUND or LOST items through a short step-by-step conversation.
Every report is reviewed by a moderator, then published to the channel
between 09:00 and 22:00 (reports submitted outside that window appear the
next morning). People respond privately through the bot, and the poster must
approve before any contact details are shared.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import FrozenSet, Iterator, NamedTuple, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import Forbidden, TelegramError
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


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing from your .env file.")
    return value


def parse_user_ids(name: str) -> FrozenSet[int]:
    """Read a comma-separated list of Telegram user IDs from .env."""
    ids = set()
    for part in (os.getenv(name) or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise RuntimeError(f"{name} contains an invalid Telegram user ID: {part!r}")
    return frozenset(ids)


BOT_TOKEN = require_env("BOT_TOKEN")
CHANNEL_ID = require_env("CHANNEL_ID")

# Moderators review every report before it is published.
# ADMIN_USER_IDS=111,222 sets several; the old single ADMIN_USER_ID still works.
MODERATOR_IDS = parse_user_ids("ADMIN_USER_IDS") | parse_user_ids("ADMIN_USER_ID")

# Publishing schedule (NFR-01): reports submitted between 09:00 and 22:00
# must appear within two hours; reports submitted outside the window appear
# the next day from 09:00.
try:
    TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
except ZoneInfoNotFoundError:
    raise RuntimeError(
        "Time zone data is missing. Run: python -m pip install -r requirements.txt "
        "(it installs tzdata, which Windows needs)."
    )
PUBLISH_START_HOUR = int(os.getenv("PUBLISH_START_HOUR", "9"))
PUBLISH_END_HOUR = int(os.getenv("PUBLISH_END_HOUR", "22"))
PUBLISH_DEADLINE = timedelta(hours=2)
REVIEW_REMINDER_BEFORE = timedelta(minutes=30)  # Nudge moderators before the deadline.
SCHEDULER_INTERVAL_SECONDS = 60

DATABASE_FILE = "lost_found.db"

MAX_CAPTION_LENGTH = 1024  # Telegram limit for photo captions.
MAX_ANNOUNCEMENT_LENGTH = 4000  # Telegram limit is 4096; keep a safety margin.
MIN_PHONE_LENGTH = 5

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------

# Report kinds
KIND_FOUND = "FOUND"
KIND_LOST = "LOST"

# Report statuses:
# pending_review -> scheduled -> publishing -> posted
#        |                          |
#        v                          v
#     rejected                    failed --(moderator Retry)--> scheduled
REPORT_PENDING_REVIEW = "pending_review"
REPORT_SCHEDULED = "scheduled"
REPORT_PUBLISHING = "publishing"
REPORT_POSTED = "posted"
REPORT_FAILED = "failed"
REPORT_REJECTED = "rejected"

# Claim types (what the responder wants to do)
CLAIM_GOT_ITEM = "got_item"
CLAIM_CONTACT_POSTER = "contact_poster"
CLAIM_FOUND_ITEM = "found_item"  # response to a LOST report

# Claim statuses
CLAIM_PENDING = "pending"
CLAIM_APPROVED = "approved"
CLAIM_DECLINED = "declined"
CLAIM_NOTIFICATION_FAILED = "notification_failed"

# Keys in context.user_data
FLOW_KEY = "report_flow"
CLAIM_KEY = "pending_claim"
REJECT_KEY = "rejecting_report"  # A moderator is typing a rejection reason.

# Reply-keyboard buttons
FOUND_BUTTON = "🕵🏻 FOUND"
LOST_BUTTON = "🔎 LOST"

# Callback data (prefixes are followed by ":<value>")
CB_CANCEL = "cancel_flow"
CB_SKIP_PHOTO = "skip_photo"
CB_PUBLISH = "publish_found"
CB_LOCATION = "location"
CB_HANDOFF = "handoff"
CB_CONTACT = "contact"
CB_CLAIM = "claim"
CB_LOST_RESPONSE = "lost_response"
CB_CLAIM_CHOICE = "claim_choice"
CB_CLAIM_DECISION = "claim_decision"
CB_MODERATE = "mod"  # mod:<approve|reject|retry>:<report id>

# Moderator actions
MOD_APPROVE = "approve"
MOD_REJECT = "reject"
MOD_RETRY = "retry"

# Contact choices
CONTACT_TELEGRAM = "telegram"
CONTACT_PHONE = "phone"
CONTACT_BOT = "bot"

# Locations. OTHER_LOCATION is a sentinel that means "the user types it".
OTHER_LOCATION = "other:"
OTHER_LOCATION_LABEL = "other (I'll write manually):"
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

# Hand-off options
HANDOFF_LEFT_IT = "Left it where I found it."
HANDOFF_TOOK_IT = "I took it, the owner contact with me"
OTHER_HANDOFF = "Other location or person (I'll write manually):"
HANDOFF_OPTIONS = [
    HANDOFF_LEFT_IT,
    HANDOFF_TOOK_IT,
    "Student affairs",
    "Entrance guards/security",
    "Library staff",
    OTHER_HANDOFF,
]
HANDOFFS_WITHOUT_DETAIL = {HANDOFF_LEFT_IT, HANDOFF_TOOK_IT}

# Reusable messages
SESSION_EXPIRED_TEXT = (
    "Your report session has expired. Please start again with /found or /lost."
)
CANCELLED_TEXT = "Cancelled. Choose FOUND or LOST below to continue, or use /start."
CONTACT_ADMIN_HINT = (
    "please contact the admin using the contact information in this bot's description."
)
LOCATION_DETAIL_EXAMPLE = (
    "(for example, UCA 204 near last row tables or near window)."
)
HANDOFF_DETAIL_PROMPT = (
    "Please clearly write the details of place you left or person handed in "
    "(for example teacher assistant from Calculus [name surname] or near "
    "vending machine in UCA 1st floor )."
)


class Step(Enum):
    """The question a user is currently answering in a report flow."""

    PHOTO = "photo"
    DESCRIPTION = "description"
    LOCATION = "location"
    LOCATION_DETAIL = "location_detail"
    HANDOFF = "handoff"
    HANDOFF_DETAIL = "handoff_detail"
    CONTACT = "contact"
    CONTACT_FALLBACK = "contact_fallback"
    PHONE = "phone"
    PREVIEW = "preview"
    SUBMITTING = "submitting"  # Guards against a double tap on Submit.


# Step numbers shown to the user ("Step 2 of 6"), per report kind.
STEP_NUMBERS = {
    KIND_FOUND: {
        Step.PHOTO: 1,
        Step.DESCRIPTION: 2,
        Step.LOCATION: 3,
        Step.HANDOFF: 4,
        Step.CONTACT: 5,
        Step.PREVIEW: 6,
    },
    KIND_LOST: {
        Step.DESCRIPTION: 1,
        Step.LOCATION: 2,
        Step.PHOTO: 3,
        Step.CONTACT: 4,
        Step.PREVIEW: 4,
    },
}
TOTAL_STEPS = {KIND_FOUND: 6, KIND_LOST: 4}


# ---------------------------------------------------------
# Conversation state
# ---------------------------------------------------------


@dataclass
class ReportFlow:
    """Answers collected while a user fills in a FOUND or LOST report."""

    kind: str
    step: Step
    user_id: int
    username: Optional[str]
    photo_file_id: Optional[str] = None
    description: str = ""
    found_place: str = ""
    found_detail: str = ""
    handoff: str = ""
    handoff_detail: str = ""
    contact_type: str = ""
    contact_value: Optional[str] = None

    @property
    def is_lost(self) -> bool:
        return self.kind == KIND_LOST


class PendingClaim(NamedTuple):
    """A responder who is about to type their verification details."""

    report_id: int
    claim_type: str


def get_flow(context: ContextTypes.DEFAULT_TYPE) -> Optional[ReportFlow]:
    return context.user_data.get(FLOW_KEY)


def clear_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(FLOW_KEY, None)
    context.user_data.pop(CLAIM_KEY, None)
    context.user_data.pop(REJECT_KEY, None)


def step_label(flow: ReportFlow, step: Step) -> str:
    number = STEP_NUMBERS[flow.kind][step]
    return f"Step {number} of {TOTAL_STEPS[flow.kind]}"


def telegram_username(user) -> Optional[str]:
    return f"@{user.username}" if user.username else None


# ---------------------------------------------------------
# Publishing schedule
# ---------------------------------------------------------


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def is_within_publish_hours(moment: Optional[datetime] = None) -> bool:
    moment = moment or now_local()
    return PUBLISH_START_HOUR <= moment.hour < PUBLISH_END_HOUR


def compute_publish_after(moment: Optional[datetime] = None) -> datetime:
    """The moment itself inside the window, otherwise the next window start."""
    moment = (moment or now_local()).astimezone(TIMEZONE)
    if is_within_publish_hours(moment):
        return moment

    window_start = moment.replace(
        hour=PUBLISH_START_HOUR, minute=0, second=0, microsecond=0
    )
    if moment.hour < PUBLISH_START_HOUR:
        return window_start
    return window_start + timedelta(days=1)


def to_db_time(moment: datetime) -> str:
    """Store times in UTC so that string comparison in SQL is always correct."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_db_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def format_local(moment: Optional[datetime]) -> str:
    if moment is None:
        return "unknown"
    return moment.astimezone(TIMEZONE).strftime("%d.%m.%Y %H:%M")


def publish_window_text() -> str:
    return f"{PUBLISH_START_HOUR:02d}:00–{PUBLISH_END_HOUR:02d}:00"


# ---------------------------------------------------------
# Database
# ---------------------------------------------------------

REPORTS_SCHEMA = """
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
"""

CLAIMS_SCHEMA = """
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
"""

# Columns added to databases created by older versions of the bot.
# (ALTER TABLE cannot add NOT NULL columns without a default.)
REPORT_MIGRATIONS = {
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
    # Moderation and publishing schedule.
    "publish_after": "TEXT",  # UTC; earliest publish time, set on submission
    "moderated_by": "INTEGER",
    "moderated_at": "TEXT",
    "reject_reason": "TEXT",
    "review_reminded_at": "TEXT",
}
CLAIM_MIGRATIONS = {
    "claim_type": "TEXT NOT NULL DEFAULT 'contact_poster'",
    "status": "TEXT NOT NULL DEFAULT 'pending'",
    "poster_message_id": "INTEGER",
}

# Column names used by older versions of the project, mapped from the
# current column. Old databases may require these NOT NULL columns.
LEGACY_REPORT_ALIASES = {
    "user_id": ("reporter_id", "owner_id", "created_by"),
    "username": ("reporter_username",),
    "kind": ("type", "report_type"),
    "description": ("title", "item_name", "item_description", "details"),
    "photo_file_id": ("photo",),
    "found_place": ("location", "place"),
    "found_detail": ("location_detail",),
    "handoff": ("handover",),
    "created_at": ("created", "date"),
}


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success (rollback on error), always close."""
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def add_missing_columns(db: sqlite3.Connection, table: str, migrations: dict) -> None:
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    for column, definition in migrations.items():
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """Create the tables and upgrade databases made by older bot versions."""
    with db_session() as db:
        db.execute(REPORTS_SCHEMA)
        add_missing_columns(db, "reports", REPORT_MIGRATIONS)
        db.execute(CLAIMS_SCHEMA)
        add_missing_columns(db, "claims", CLAIM_MIGRATIONS)


def insert_row(db: sqlite3.Connection, table: str, values: dict) -> int:
    """Insert a row, adapting to whatever columns the table actually has.

    Older databases may contain extra NOT NULL columns. Known columns get
    their value from `values` (keys must be lowercase); unknown required
    columns get a neutral fallback so the insert never fails because of them.
    """
    columns, parameters = [], []

    for column in db.execute(f"PRAGMA table_info({table})"):
        name = column["name"]
        column_type = (column["type"] or "").upper()
        is_required = column["notnull"] and column["dflt_value"] is None

        if column["pk"] and "INT" in column_type:
            continue  # SQLite generates the integer primary key.

        if name.lower() in values:
            value = values[name.lower()]
            if value is None and is_required:
                value = ""
        elif is_required:
            value = 0 if "INT" in column_type else ""
        else:
            continue  # Nullable or defaulted column we know nothing about.

        columns.append('"' + name.replace('"', '""') + '"')
        parameters.append(value)

    if not columns:
        raise sqlite3.OperationalError(f"The {table} table has no insertable columns.")

    placeholders = ", ".join("?" for _ in columns)
    cursor = db.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        parameters,
    )
    return cursor.lastrowid


def build_report_values(flow: ReportFlow, user, created_at: str) -> dict:
    values = {
        "user_id": user.id,
        "username": telegram_username(user),
        "kind": flow.kind,
        "description": flow.description,
        "photo_file_id": flow.photo_file_id,
        "found_place": flow.found_place,
        "found_detail": flow.found_detail,
        "handoff": flow.handoff,
        "handoff_detail": flow.handoff_detail or None,
        "contact_type": flow.contact_type or CONTACT_BOT,
        "contact_value": flow.contact_value,
        "created_at": created_at,
        # Nothing is published until a moderator approves it.
        "status": REPORT_PENDING_REVIEW,
        "publish_after": to_db_time(compute_publish_after()),
        "channel_message_id": None,
    }

    # Fill the legacy column names with the same values.
    for column, aliases in LEGACY_REPORT_ALIASES.items():
        for alias in aliases:
            values[alias] = values[column]

    values["handoff_location"] = values["handoff_detail"] or flow.handoff
    values["contact"] = flow.contact_value or values["contact_type"]
    values["contact_info"] = values["contact"]
    return values


def save_report(flow: ReportFlow, user, created_at: str) -> int:
    with db_session() as db:
        return insert_row(db, "reports", build_report_values(flow, user, created_at))


def save_claim(report_id: int, claimant, detail: str, claim_type: str) -> int:
    values = {
        "report_id": report_id,
        "claimant_id": claimant.id,
        "claimant_username": telegram_username(claimant),
        "detail": detail,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "claim_type": claim_type,
        "status": CLAIM_PENDING,
        "poster_message_id": None,
    }
    with db_session() as db:
        return insert_row(db, "claims", values)


def fetch_report(report_id: int) -> Optional[sqlite3.Row]:
    with db_session() as db:
        return db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()


def fetch_report_ids(where: str, parameters: tuple = ()) -> list:
    with db_session() as db:
        rows = db.execute(f"SELECT id FROM reports WHERE {where}", parameters)
        return [row["id"] for row in rows]


def change_report_status(
    report_id: int, from_status: str, to_status: str, **fields
) -> bool:
    """Move a report between statuses atomically.

    Succeeds only if the report is still in `from_status`, so two moderators
    (or a moderator and the scheduler) can never apply the same step twice.
    """
    assignments = ", ".join(["status = ?"] + [f"{name} = ?" for name in fields])
    with db_session() as db:
        cursor = db.execute(
            f"UPDATE reports SET {assignments} WHERE id = ? AND status = ?",
            (to_status, *fields.values(), report_id, from_status),
        )
        return cursor.rowcount == 1


def fetch_posted_report(report_id: int) -> Optional[sqlite3.Row]:
    with db_session() as db:
        return db.execute(
            "SELECT * FROM reports WHERE id = ? AND status = ?",
            (report_id, REPORT_POSTED),
        ).fetchone()


def update_report_status(
    report_id: int, status: str, channel_message_id: Optional[int] = None
) -> None:
    """Best-effort status update; a failure is logged, never raised."""
    try:
        with db_session() as db:
            if channel_message_id is None:
                db.execute(
                    "UPDATE reports SET status = ? WHERE id = ?",
                    (status, report_id),
                )
            else:
                db.execute(
                    "UPDATE reports SET status = ?, channel_message_id = ? WHERE id = ?",
                    (status, channel_message_id, report_id),
                )
    except sqlite3.Error:
        logger.exception("Could not set status %r on report %s", status, report_id)


def set_claim_status(claim_id: int, status: str) -> None:
    with db_session() as db:
        db.execute("UPDATE claims SET status = ? WHERE id = ?", (status, claim_id))


def set_claim_poster_message(claim_id: int, message_id: int) -> None:
    with db_session() as db:
        db.execute(
            "UPDATE claims SET poster_message_id = ? WHERE id = ?",
            (message_id, claim_id),
        )


def fetch_claim_with_report(claim_id: int):
    """Return (claim, report); either may be None if the row is missing."""
    with db_session() as db:
        claim = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        report = None
        if claim:
            report = db.execute(
                "SELECT * FROM reports WHERE id = ?", (claim["report_id"],)
            ).fetchone()
    return claim, report


def report_kind(report: sqlite3.Row) -> str:
    return (report["kind"] or KIND_FOUND).upper()


# ---------------------------------------------------------
# Keyboards
# ---------------------------------------------------------


def button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=callback_data)


def inline_keyboard(*rows: Sequence[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(row) for row in rows])


def cancel_row() -> list:
    return [button("Cancel", CB_CANCEL)]


def main_keyboard() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard shown after the user presses Start."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(FOUND_BUTTON), KeyboardButton(LOST_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Choose FOUND or LOST",
    )


def photo_keyboard() -> InlineKeyboardMarkup:
    return inline_keyboard([button("Skip photo", CB_SKIP_PHOTO)], cancel_row())


def location_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        button(
            OTHER_LOCATION_LABEL if location == OTHER_LOCATION else location,
            f"{CB_LOCATION}:{index}",
        )
        for index, location in enumerate(FOUND_LOCATIONS)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return inline_keyboard(*rows, cancel_row())


def handoff_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [button(option, f"{CB_HANDOFF}:{index}")]
        for index, option in enumerate(HANDOFF_OPTIONS)
    ]
    return inline_keyboard(*rows, cancel_row())


def contact_keyboard() -> InlineKeyboardMarkup:
    return inline_keyboard(
        [button("Use my Telegram username", f"{CB_CONTACT}:{CONTACT_TELEGRAM}")],
        [button("Use a phone number", f"{CB_CONTACT}:{CONTACT_PHONE}")],
        [button("Contact me through the bot", f"{CB_CONTACT}:{CONTACT_BOT}")],
        cancel_row(),
    )


def contact_fallback_keyboard() -> InlineKeyboardMarkup:
    return inline_keyboard(
        [button("Enter a phone number", f"{CB_CONTACT}:{CONTACT_PHONE}")],
        [button("Contact me through the bot", f"{CB_CONTACT}:{CONTACT_BOT}")],
        cancel_row(),
    )


def preview_keyboard() -> InlineKeyboardMarkup:
    return inline_keyboard([button("Submit for review", CB_PUBLISH)], cancel_row())


def moderation_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return inline_keyboard(
        [
            button("✅ Approve", f"{CB_MODERATE}:{MOD_APPROVE}:{report_id}"),
            button("❌ Reject", f"{CB_MODERATE}:{MOD_REJECT}:{report_id}"),
        ]
    )


def retry_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return inline_keyboard(
        [button("🔁 Retry publishing", f"{CB_MODERATE}:{MOD_RETRY}:{report_id}")]
    )


def claim_keyboard(report_id: int) -> InlineKeyboardMarkup:
    """Channel button on a FOUND announcement."""
    return inline_keyboard([button("I think this is mine", f"{CB_CLAIM}:{report_id}")])


def lost_response_keyboard(report_id: int) -> InlineKeyboardMarkup:
    """Channel button on a LOST announcement."""
    return inline_keyboard(
        [button("I found this item", f"{CB_LOST_RESPONSE}:{report_id}")]
    )


def claim_choice_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return inline_keyboard(
        [
            button(
                "I got my lost item",
                f"{CB_CLAIM_CHOICE}:{report_id}:{CLAIM_GOT_ITEM}",
            )
        ],
        [
            button(
                "I wanna contact the person who posted this",
                f"{CB_CLAIM_CHOICE}:{report_id}:{CLAIM_CONTACT_POSTER}",
            )
        ],
    )


def claim_decision_keyboard(claim_id: int) -> InlineKeyboardMarkup:
    return inline_keyboard(
        [
            button(
                "Approve and share my contact",
                f"{CB_CLAIM_DECISION}:{claim_id}:approve",
            )
        ],
        [
            button(
                "Decline contact sharing",
                f"{CB_CLAIM_DECISION}:{claim_id}:decline",
            )
        ],
    )


# ---------------------------------------------------------
# Announcement formatting
# ---------------------------------------------------------


def format_place(flow: ReportFlow) -> str:
    detail = flow.found_detail.strip()

    # Never show the word "Other" in the announcement.
    if flow.found_place == OTHER_LOCATION:
        return detail

    # Avoid output such as "MATH — MATH 206".
    if detail.lower().startswith(flow.found_place.lower()):
        return detail

    return f"{flow.found_place} — {detail}"


def format_handoff(flow: ReportFlow) -> str:
    detail = (flow.handoff_detail or "").strip()

    # For "Other", show only what the user typed.
    if flow.handoff == OTHER_HANDOFF:
        return detail

    return f"{flow.handoff} — {detail}" if detail else flow.handoff


def format_contact(flow: ReportFlow) -> str:
    if flow.contact_type in {CONTACT_TELEGRAM, CONTACT_PHONE}:
        return flow.contact_value or "Contact details were not provided."

    if flow.is_lost:
        return "Use the “I found this item” button below."
    return "Use the “I think this is mine” button below."


def make_announcement(flow: ReportFlow, reported_at: Optional[str] = None) -> str:
    """Build the channel post. `reported_at` is the saved created_at value;
    without it (the preview) the current time is shown."""
    photo_line = "📷 Photo: Attached\n" if flow.photo_file_id else ""
    if reported_at:
        reported = str(reported_at)[:16].replace("T", " ")
    else:
        reported = datetime.now().strftime("%Y-%m-%d %H:%M")
    item = flow.description.strip()
    place = format_place(flow)
    contact = format_contact(flow)

    if flow.is_lost:
        return (
            "#LOST\n\n"
            f"🔎 Item: {item}\n"
            f"{photo_line}"
            f"📍 Probably lost at: {place}\n"
            f"📞 Contact: {contact}\n"
            f"📅 Reported: {reported}"
        )

    return (
        "#FOUND\n\n"
        f"🔎 Item: {item}\n"
        f"{photo_line}"
        f"📍 Found at: {place}\n"
        f"📦 Left/handed in: {format_handoff(flow)}\n"
        f"📞 Contact: {contact}\n"
        f"📅 Reported: {reported}"
    )


# ---------------------------------------------------------
# Step prompts
# Each prompt records the new step on the flow, then asks the question.
# ---------------------------------------------------------


async def send_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    flow: ReportFlow,
    step: Step,
    text: str,
    reply_markup=None,
) -> None:
    flow.step = step
    await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=reply_markup
    )


async def prompt_photo(chat_id, context, flow: ReportFlow) -> None:
    label = step_label(flow, Step.PHOTO)
    if flow.is_lost:
        text = (
            f"{label}\n\n"
            "If possible, attach a photo of the lost item, or skip this step."
        )
    else:
        text = (
            f"{label}\n\n"
            "Would you like to attach a photo of the item?\n"
            "Send a photo or press “Skip photo”."
        )
    await send_prompt(context, chat_id, flow, Step.PHOTO, text, photo_keyboard())


async def prompt_description(chat_id, context, flow: ReportFlow) -> None:
    label = step_label(flow, Step.DESCRIPTION)
    if flow.is_lost:
        text = f"{label}\n\nPlease describe the item you lost."
    else:
        text = f"{label}\n\nWhat did you find left? Give short description"
    await send_prompt(context, chat_id, flow, Step.DESCRIPTION, text)


async def prompt_location(chat_id, context, flow: ReportFlow) -> None:
    label = step_label(flow, Step.LOCATION)
    if flow.is_lost:
        question = "Where do you think you lost it? Choose the most likely place:"
    else:
        question = "Where did you find it? Choose the place:"
    await send_prompt(
        context, chat_id, flow, Step.LOCATION, f"{label}\n\n{question}",
        location_keyboard(),
    )


async def prompt_handoff(chat_id, context, flow: ReportFlow) -> None:
    text = (
        f"{step_label(flow, Step.HANDOFF)}\n\n"
        "Did you leave it somewhere or hand in the item?\n"
        "Choose the option that best describes what happened:"
    )
    await send_prompt(context, chat_id, flow, Step.HANDOFF, text, handoff_keyboard())


async def prompt_contact(chat_id, context, flow: ReportFlow) -> None:
    label = step_label(flow, Step.CONTACT)
    if flow.is_lost:
        question = "If someone finds your item, how should they contact you?"
    else:
        question = "How should the owner contact you?"
    text = (
        f"{label}\n\n{question}\n"
        "Your Telegram username will be filled in automatically if you have one."
    )
    await send_prompt(
        context, chat_id, flow, Step.CONTACT, text, contact_keyboard()
    )


async def show_preview(chat_id, context, flow: ReportFlow) -> None:
    text = (
        f"{step_label(flow, Step.PREVIEW)} — Preview your {flow.kind} announcement\n\n"
        "Please check the details before submitting.\n\n"
        f"{make_announcement(flow)}\n\n"
        "———\n"
        "A moderator reviews every report before it is posted. Reports are "
        f"published between {publish_window_text()}; reports submitted later "
        "appear the next morning."
    )
    await send_prompt(context, chat_id, flow, Step.PREVIEW, text, preview_keyboard())


async def continue_after_photo(chat_id, context, flow: ReportFlow) -> None:
    if flow.is_lost:
        await prompt_contact(chat_id, context, flow)
    else:
        await prompt_description(chat_id, context, flow)


async def safe_send_message(context, chat_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except TelegramError:
        logger.warning("Could not send a message to chat %s", chat_id)


async def require_flow(update: Update, context) -> Optional[ReportFlow]:
    """Return the active flow, or tell the user their session expired."""
    flow = get_flow(context)
    if not flow:
        await update.effective_message.reply_text(SESSION_EXPIRED_TEXT)
    return flow


# ---------------------------------------------------------
# Commands
# ---------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Do not bring the menu back in the middle of an active report.
    if get_flow(context):
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


async def start_report(update: Update, context, kind: str) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    flow = ReportFlow(
        kind=kind,
        step=Step.PHOTO if kind == KIND_FOUND else Step.DESCRIPTION,
        user_id=user.id,
        username=telegram_username(user),
    )
    context.user_data[FLOW_KEY] = flow

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Starting your {kind} report. The menu will return when you publish or cancel.",
        reply_markup=ReplyKeyboardRemove(),
    )
    if flow.is_lost:
        await prompt_description(chat_id, context, flow)
    else:
        await prompt_photo(chat_id, context, flow)


async def start_found(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_report(update, context, KIND_FOUND)


async def start_lost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_report(update, context, KIND_LOST)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_session(context)
    await update.effective_message.reply_text(
        CANCELLED_TEXT, reply_markup=main_keyboard()
    )


# ---------------------------------------------------------
# Photo message
# ---------------------------------------------------------


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = get_flow(context)
    if not flow or flow.step != Step.PHOTO:
        await update.effective_message.reply_text(
            "To attach a photo to a new report, start with /found or /lost."
        )
        return

    flow.photo_file_id = update.effective_message.photo[-1].file_id
    await update.effective_message.reply_text("Photo attached successfully.")
    await continue_after_photo(update.effective_chat.id, context, flow)


# ---------------------------------------------------------
# Text messages
# Each handler receives (update, context, flow, text) for one Step.
# ---------------------------------------------------------


async def on_text_at_photo_step(update, context, flow, text):
    await update.effective_message.reply_text(
        "Please send a photo or press “Skip photo”.",
        reply_markup=photo_keyboard(),
    )


async def on_text_at_description(update, context, flow, text):
    if not text:
        await update.effective_message.reply_text(
            "Please enter a short description of the item."
        )
        return

    flow.description = text
    await prompt_location(update.effective_chat.id, context, flow)


async def on_text_at_location_detail(update, context, flow, text):
    if not text:
        await update.effective_message.reply_text("Please specify the exact place.")
        return

    flow.found_detail = text
    chat_id = update.effective_chat.id
    if flow.is_lost:
        await prompt_photo(chat_id, context, flow)
    else:
        await prompt_handoff(chat_id, context, flow)


async def on_text_at_handoff_detail(update, context, flow, text):
    if not text:
        await update.effective_message.reply_text(
            "Please enter the details of the place or person."
        )
        return

    flow.handoff_detail = text
    await prompt_contact(update.effective_chat.id, context, flow)


async def on_text_at_phone(update, context, flow, text):
    if len(text) < MIN_PHONE_LENGTH:
        await update.effective_message.reply_text(
            "That number seems too short. Please enter a valid "
            "contact number, or use /cancel to stop."
        )
        return

    flow.contact_type = CONTACT_PHONE
    flow.contact_value = text
    await show_preview(update.effective_chat.id, context, flow)


async def on_text_at_preview(update, context, flow, text):
    await update.effective_message.reply_text(
        "Your report is ready. Use the buttons in the preview "
        "to publish it or cancel."
    )


async def on_text_at_contact_fallback(update, context, flow, text):
    await update.effective_message.reply_text(
        "Please choose a contact option using the buttons below.",
        reply_markup=contact_fallback_keyboard(),
    )


TEXT_STEP_HANDLERS = {
    Step.PHOTO: on_text_at_photo_step,
    Step.DESCRIPTION: on_text_at_description,
    Step.LOCATION_DETAIL: on_text_at_location_detail,
    Step.HANDOFF_DETAIL: on_text_at_handoff_detail,
    Step.PHONE: on_text_at_phone,
    Step.PREVIEW: on_text_at_preview,
    Step.CONTACT_FALLBACK: on_text_at_contact_fallback,
}


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    text = (message.text or "").strip()

    # A moderator typing the reason for a rejection comes first.
    if context.user_data.get(REJECT_KEY):
        await receive_reject_reason(update, context)
        return

    # A claim's identifying details are handled before anything else.
    if context.user_data.get(CLAIM_KEY):
        await receive_claim_detail(update, context)
        return

    # Menu buttons win over interpreting the text as a report answer.
    normalized = text.casefold()
    if normalized in {FOUND_BUTTON.casefold(), "found"}:
        await start_found(update, context)
        return
    if normalized in {LOST_BUTTON.casefold(), "lost"}:
        await start_lost(update, context)
        return

    flow = get_flow(context)
    if not flow:
        await message.reply_text(
            "Choose FOUND or LOST below, or use /start to show the menu.",
            reply_markup=main_keyboard(),
        )
        return

    handler = TEXT_STEP_HANDLERS.get(flow.step)
    if handler is None:
        await message.reply_text("Please use the buttons or follow the latest instruction.")
        return

    await handler(update, context, flow, text)


# ---------------------------------------------------------
# Button callbacks: report flow
# ---------------------------------------------------------


async def on_cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_session(context)
    await query.message.reply_text(CANCELLED_TEXT, reply_markup=main_keyboard())


async def on_skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    flow = get_flow(context)
    if not flow or flow.step != Step.PHOTO:
        await query.message.reply_text(
            "There is no active photo step. Start a report with /found or /lost."
        )
        return

    flow.photo_file_id = None
    await continue_after_photo(query.message.chat_id, context, flow)


async def on_location_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    flow = await require_flow(update, context)
    if not flow:
        return

    index = int(context.match.group(1))
    if index >= len(FOUND_LOCATIONS):
        await query.message.reply_text("Invalid location selection.")
        return

    selected = FOUND_LOCATIONS[index]
    flow.found_place = selected
    flow.step = Step.LOCATION_DETAIL
    await query.message.reply_text(build_location_detail_prompt(flow, selected))


def build_location_detail_prompt(flow: ReportFlow, selected: str) -> str:
    is_other = selected == OTHER_LOCATION

    if flow.is_lost:
        if is_other:
            return (
                "Where do you think you lost it? "
                "Please write the most likely exact place."
            )
        return (
            f"You chose {selected}.\n\n"
            "Please specify the most likely exact place where you lost it "
            "(for example, UCA 204 near the window)."
        )

    if is_other:
        return f"Please specify the exact place {LOCATION_DETAIL_EXAMPLE}"
    return f"You chose {selected}.\n\nPlease specify the exact place {LOCATION_DETAIL_EXAMPLE}"


async def on_handoff_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    flow = await require_flow(update, context)
    if not flow:
        return

    index = int(context.match.group(1))
    if index >= len(HANDOFF_OPTIONS):
        await query.message.reply_text("Invalid selection.")
        return

    selected = HANDOFF_OPTIONS[index]
    flow.handoff = selected
    flow.handoff_detail = ""

    if selected in HANDOFFS_WITHOUT_DETAIL:
        await prompt_contact(query.message.chat_id, context, flow)
        return

    flow.step = Step.HANDOFF_DETAIL
    if selected == OTHER_HANDOFF:
        prompt = HANDOFF_DETAIL_PROMPT
    else:
        prompt = f"You selected {selected}.\n\n{HANDOFF_DETAIL_PROMPT}"
    await query.message.reply_text(prompt)


async def on_contact_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    flow = await require_flow(update, context)
    if not flow:
        return

    choice = context.match.group(1)
    chat_id = query.message.chat_id

    if choice == CONTACT_TELEGRAM:
        username = telegram_username(query.from_user)
        if username:
            flow.contact_type = CONTACT_TELEGRAM
            flow.contact_value = username
            await show_preview(chat_id, context, flow)
        else:
            flow.step = Step.CONTACT_FALLBACK
            await query.message.reply_text(
                "You don't have a Telegram username set up, so I can't "
                "fill one in automatically.\n\n"
                "You can enter a phone number or let the owner contact "
                "you through the bot.",
                reply_markup=contact_fallback_keyboard(),
            )

    elif choice == CONTACT_PHONE:
        flow.step = Step.PHONE
        audience = "the person who finds your item" if flow.is_lost else "the owner"
        await query.message.reply_text(
            f"Please enter the phone number you want to share with {audience}.\n\n"
            "Only enter a number you are comfortable publishing in the announcement."
        )

    elif choice == CONTACT_BOT:
        flow.contact_type = CONTACT_BOT
        flow.contact_value = None
        await show_preview(chat_id, context, flow)


# ---------------------------------------------------------
# Publishing
# ---------------------------------------------------------


async def post_to_channel(
    context, flow: ReportFlow, announcement: str, keyboard
) -> int:
    """Send the announcement to the channel and return the message id."""
    if flow.photo_file_id and len(announcement) <= MAX_CAPTION_LENGTH:
        posted = await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=flow.photo_file_id,
            caption=announcement,
            reply_markup=keyboard,
        )
        return posted.message_id

    # The caption would be too long: send the photo alone, then the full
    # text together with its button.
    if flow.photo_file_id:
        await context.bot.send_photo(chat_id=CHANNEL_ID, photo=flow.photo_file_id)

    posted = await context.bot.send_message(
        chat_id=CHANNEL_ID, text=announcement, reply_markup=keyboard
    )
    return posted.message_id


async def on_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submit the report for moderation. Nothing reaches the channel yet."""
    query = update.callback_query
    await query.answer()

    flow = get_flow(context)
    if not flow or flow.step != Step.PREVIEW:
        await query.message.reply_text(
            "There is no ready-to-submit report. Please start again with /found or /lost."
        )
        return

    # A second tap while saving must not create a second report (NFR-05).
    flow.step = Step.SUBMITTING

    created_at = datetime.now().isoformat(timespec="seconds")
    try:
        report_id = save_report(flow, query.from_user, created_at)
    except sqlite3.Error as error:
        flow.step = Step.PREVIEW
        logger.exception("Database error while saving %s report", flow.kind)
        await query.message.reply_text(
            "I couldn't save this report to the database. "
            "Your answers are still in this chat; please try Submit again.\n\n"
            f"Database error: {error}"
        )
        return

    context.user_data.pop(FLOW_KEY, None)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass  # The buttons were already removed; nothing to do.

    if not MODERATOR_IDS:
        # No moderators configured: skip review, keep the schedule.
        await query.message.reply_text(
            f"Your {flow.kind} report #{report_id} has been accepted.",
            reply_markup=main_keyboard(),
        )
        await approve_report(context, report_id, moderator_id=None)
        return

    await query.message.reply_text(
        f"✅ Your {flow.kind} report #{report_id} was sent to the moderators for review.\n\n"
        f"Approved reports are published between {publish_window_text()}; "
        "reports submitted later appear the next morning. "
        "I'll message you when it is published.\n\n"
        "Choose FOUND or LOST below if you want to make another report.",
        reply_markup=main_keyboard(),
    )
    await send_review_request(context, report_id)


# ---------------------------------------------------------
# Moderation: review queue
# ---------------------------------------------------------


def is_moderator(user) -> bool:
    return user is not None and user.id in MODERATOR_IDS


def flow_from_report(report: sqlite3.Row) -> ReportFlow:
    """Rebuild the report answers from the database, to format the post later."""
    return ReportFlow(
        kind=report_kind(report),
        step=Step.PREVIEW,
        user_id=report["user_id"],
        username=report["username"],
        photo_file_id=report["photo_file_id"],
        description=report["description"] or "",
        found_place=report["found_place"] or "",
        found_detail=report["found_detail"] or "",
        handoff=report["handoff"] or "",
        handoff_detail=report["handoff_detail"] or "",
        contact_type=report["contact_type"] or "",
        contact_value=report["contact_value"],
    )


def build_announcement(report: sqlite3.Row) -> str:
    announcement = make_announcement(flow_from_report(report), report["created_at"])
    if len(announcement) > MAX_ANNOUNCEMENT_LENGTH:
        announcement = announcement[:3980].rstrip() + "\n…(announcement shortened)"
    return announcement


def publish_deadline(report: sqlite3.Row) -> Optional[datetime]:
    """NFR-01: published within two hours of the earliest publish time."""
    earliest = from_db_time(report["publish_after"])
    return earliest + PUBLISH_DEADLINE if earliest else None


def planned_publish_time(report: sqlite3.Row) -> datetime:
    """When the report would go out if it were approved right now."""
    earliest = from_db_time(report["publish_after"]) or now_local()
    return compute_publish_after(max(earliest, now_local()))


def make_review_text(report: sqlite3.Row) -> str:
    kind = report_kind(report)
    reporter = report["username"] or "no username"
    planned = planned_publish_time(report)
    if planned <= now_local():
        when = "If approved now, it is published immediately."
    else:
        when = f"If approved now, it is published at {format_local(planned)}."

    return (
        f"🛡 #{kind} report #{report['id']} — waiting for review\n"
        f"From: {reporter} (ID {report['user_id']})\n"
        f"Publish by: {format_local(publish_deadline(report))} (2-hour rule)\n\n"
        "Channel post will look like this:\n"
        "———\n"
        f"{build_announcement(report)}\n"
        "———\n"
        f"{when}"
    )


async def notify_moderators(context, text: str, reply_markup=None) -> None:
    for moderator_id in MODERATOR_IDS:
        try:
            await context.bot.send_message(
                chat_id=moderator_id, text=text, reply_markup=reply_markup
            )
        except TelegramError:
            logger.warning(
                "Could not message moderator %s. They must open the bot and "
                "press Start once.",
                moderator_id,
            )


async def send_review_request(
    context, report_id: int, moderator_ids: Optional[Sequence[int]] = None
) -> None:
    """Send a pending report with Approve / Reject buttons to moderators."""
    report = fetch_report(report_id)
    if not report:
        return

    text = make_review_text(report)
    keyboard = moderation_keyboard(report_id)
    photo = report["photo_file_id"]

    for moderator_id in moderator_ids or MODERATOR_IDS:
        try:
            if photo and len(text) <= MAX_CAPTION_LENGTH:
                await context.bot.send_photo(
                    chat_id=moderator_id, photo=photo, caption=text,
                    reply_markup=keyboard,
                )
                continue
            if photo:
                await context.bot.send_photo(chat_id=moderator_id, photo=photo)
            await context.bot.send_message(
                chat_id=moderator_id, text=text, reply_markup=keyboard
            )
        except TelegramError:
            logger.warning(
                "Could not send report %s to moderator %s. They must open the "
                "bot and press Start once.",
                report_id,
                moderator_id,
            )


async def approve_report(
    context, report_id: int, moderator_id: Optional[int]
) -> Optional[datetime]:
    """Approve a pending report and schedule it.

    Returns the publish time, or None if the report was no longer pending
    (for example, another moderator handled it first).
    """
    report = fetch_report(report_id)
    if not report:
        return None

    publish_at = planned_publish_time(report)
    approved = change_report_status(
        report_id,
        REPORT_PENDING_REVIEW,
        REPORT_SCHEDULED,
        publish_after=to_db_time(publish_at),
        moderated_by=moderator_id,
        moderated_at=to_db_time(now_local()),
    )
    if not approved:
        return None

    if publish_at <= now_local():
        await publish_report(context, report_id)
    else:
        await safe_send_message(
            context,
            report["user_id"],
            f"Your #{report_kind(report)} report #{report_id} was approved. "
            f"It will be published in the channel at {format_local(publish_at)}.",
        )
    return publish_at


async def remove_buttons(query) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass  # The buttons were already removed; nothing to do.


async def on_moderation_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A moderator presses Approve, Reject or Retry."""
    query = update.callback_query
    moderator = query.from_user
    action = context.match.group(1)
    report_id = int(context.match.group(2))

    if not is_moderator(moderator):
        await query.answer("Only moderators can do this.", show_alert=True)
        return

    moderator_name = telegram_username(moderator) or str(moderator.id)

    if action == MOD_APPROVE:
        publish_at = await approve_report(context, report_id, moderator.id)
        await remove_buttons(query)
        if publish_at is None:
            report = fetch_report(report_id)
            status = report["status"] if report else "deleted"
            await query.answer(
                f"Report #{report_id} was already handled ({status}).", show_alert=True
            )
            return

        await query.answer("Approved.")
        report = fetch_report(report_id)
        if report["status"] == REPORT_POSTED:
            outcome = "published to the channel now"
        elif report["status"] == REPORT_FAILED:
            outcome = "approved, but publishing failed (see the message below)"
        else:
            outcome = f"scheduled for {format_local(publish_at)}"
        await query.message.reply_text(
            f"✅ Report #{report_id} approved by {moderator_name}: {outcome}."
        )
        return

    if action == MOD_REJECT:
        report = fetch_report(report_id)
        if not report or report["status"] != REPORT_PENDING_REVIEW:
            await remove_buttons(query)
            await query.answer(f"Report #{report_id} was already handled.", show_alert=True)
            return

        context.user_data[REJECT_KEY] = report_id
        await query.answer()
        await query.message.reply_text(
            f"Send the reason for rejecting report #{report_id}. "
            "The reporter will see it.\n\n"
            "Send - to reject without a reason, or /cancel to keep the report pending."
        )
        return

    # MOD_RETRY: publish a failed report again, right away.
    retried = change_report_status(
        report_id, REPORT_FAILED, REPORT_SCHEDULED,
        publish_after=to_db_time(now_local()),
    )
    await remove_buttons(query)
    if not retried:
        await query.answer("This report is not waiting for a retry.", show_alert=True)
        return

    await query.answer("Retrying…")
    published = await publish_report(context, report_id)
    await query.message.reply_text(
        f"Report #{report_id} published."
        if published
        else f"Report #{report_id} still could not be published."
    )


async def receive_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    moderator = update.effective_user
    report_id = context.user_data.pop(REJECT_KEY, None)
    if not report_id or not is_moderator(moderator):
        return

    reason = (message.text or "").strip()
    if reason in {"", "-"}:
        reason = None

    rejected = change_report_status(
        report_id,
        REPORT_PENDING_REVIEW,
        REPORT_REJECTED,
        moderated_by=moderator.id,
        moderated_at=to_db_time(now_local()),
        reject_reason=reason,
    )
    if not rejected:
        await message.reply_text(f"Report #{report_id} was already handled by someone else.")
        return

    await message.reply_text(f"❌ Report #{report_id} rejected.")

    report = fetch_report(report_id)
    if report:
        text = (
            f"Your #{report_kind(report)} report #{report_id} was not approved "
            "by the moderators."
        )
        if reason:
            text += f"\n\nReason: {reason}"
        text += "\n\nYou can submit a corrected report with FOUND or LOST."
        await safe_send_message(context, report["user_id"], text)


async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pending (moderators): reports waiting for review, scheduled or failed."""
    user = update.effective_user
    message = update.effective_message
    if not is_moderator(user):
        await message.reply_text("This command is for moderators only.")
        return

    pending = fetch_report_ids(
        "status = ? ORDER BY id", (REPORT_PENDING_REVIEW,)
    )
    scheduled = fetch_report_ids(
        "status = ? ORDER BY publish_after, id", (REPORT_SCHEDULED,)
    )
    failed = fetch_report_ids("status = ? ORDER BY id", (REPORT_FAILED,))

    lines = [f"🛡 Waiting for review: {len(pending)}"]
    if scheduled:
        lines.append(f"⏰ Scheduled: {len(scheduled)}")
        for report_id in scheduled:
            report = fetch_report(report_id)
            lines.append(
                f"  • #{report_id} at {format_local(from_db_time(report['publish_after']))}"
            )
    if failed:
        lines.append(f"⚠️ Failed to publish: {len(failed)}")
    await message.reply_text("\n".join(lines))

    for report_id in pending:
        await send_review_request(context, report_id, moderator_ids=[user.id])
    for report_id in failed:
        await message.reply_text(
            f"Report #{report_id} could not be published.",
            reply_markup=retry_keyboard(report_id),
        )


# ---------------------------------------------------------
# Publishing (on approval, or from the scheduler)
# ---------------------------------------------------------


async def publish_report(context, report_id: int) -> bool:
    """Post one scheduled report to the channel. Returns True on success."""
    # Lock the report first so it can never be posted twice.
    if not change_report_status(report_id, REPORT_SCHEDULED, REPORT_PUBLISHING):
        return False

    report = fetch_report(report_id)
    flow = flow_from_report(report)
    keyboard = (
        lost_response_keyboard(report_id) if flow.is_lost else claim_keyboard(report_id)
    )

    try:
        channel_message_id = await post_to_channel(
            context, flow, build_announcement(report), keyboard
        )
    except TelegramError as error:
        logger.exception("Could not publish report %s", report_id)
        update_report_status(report_id, REPORT_FAILED)
        await notify_moderators(
            context,
            f"⚠️ Report #{report_id} was approved but could not be posted. "
            "Check that the bot is a channel administrator with permission "
            f"to post.\n\nTelegram error: {error}",
            reply_markup=retry_keyboard(report_id),
        )
        return False

    # The post is live, so a failure here must not be reported as a failed publish.
    update_report_status(report_id, REPORT_POSTED, channel_message_id)
    await safe_send_message(
        context,
        report["user_id"],
        f"📢 Your #{flow.kind} report #{report_id} is now published in the channel.",
    )
    return True


async def remind_moderators(context) -> None:
    """Nudge moderators about reports close to their two-hour deadline."""
    now = now_local()
    for report_id in fetch_report_ids(
        "status = ? AND review_reminded_at IS NULL ORDER BY id",
        (REPORT_PENDING_REVIEW,),
    ):
        report = fetch_report(report_id)
        deadline = publish_deadline(report)
        if deadline is None or deadline - now > REVIEW_REMINDER_BEFORE:
            continue

        with db_session() as db:
            db.execute(
                "UPDATE reports SET review_reminded_at = ? WHERE id = ?",
                (to_db_time(now), report_id),
            )
        await notify_moderators(
            context,
            f"⏰ Report #{report_id} is still waiting for review. It must be "
            f"published by {format_local(deadline)}. Use /pending to see it.",
        )


async def run_schedule(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduler job, every minute: publish due reports, send reminders."""
    if not is_within_publish_hours():
        return

    due = fetch_report_ids(
        "status = ? AND publish_after <= ? ORDER BY publish_after, id",
        (REPORT_SCHEDULED, to_db_time(now_local())),
    )
    for report_id in due:
        await publish_report(context, report_id)

    if MODERATOR_IDS:
        await remind_moderators(context)


# ---------------------------------------------------------
# Button callbacks: private claim flow
# ---------------------------------------------------------


async def on_claim_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Someone presses "I think this is mine" on a FOUND announcement."""
    query = update.callback_query
    claimant = query.from_user
    report_id = int(context.match.group(1))

    report = fetch_posted_report(report_id)
    if not report:
        await query.answer("This announcement is no longer available.", show_alert=True)
        return
    if report["user_id"] == claimant.id:
        await query.answer("This is your own report.", show_alert=True)
        return

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


async def on_lost_response_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Someone presses "I found this item" on a LOST announcement."""
    query = update.callback_query
    finder = query.from_user
    report_id = int(context.match.group(1))

    report = fetch_posted_report(report_id)
    if not report or report_kind(report) != KIND_LOST:
        await query.answer(
            "This lost-item announcement is no longer available.", show_alert=True
        )
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
        await query.answer(
            "I couldn't message you privately. Open the bot and try again.",
            show_alert=True,
        )
        return

    context.user_data[CLAIM_KEY] = PendingClaim(report_id, CLAIM_FOUND_ITEM)
    await query.answer("Check your private chat with the bot.")


async def on_claim_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The claimant picks what they want to do with a FOUND announcement."""
    query = update.callback_query
    claimant = query.from_user
    report_id = int(context.match.group(1))
    claim_type = context.match.group(2)

    report = fetch_posted_report(report_id)
    if not report:
        await query.answer("This announcement is no longer available.", show_alert=True)
        return
    if report["user_id"] == claimant.id:
        await query.answer("This is your own report.", show_alert=True)
        return

    context.user_data[CLAIM_KEY] = PendingClaim(report_id, claim_type)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass  # The buttons were already removed; nothing to do.

    if claim_type == CLAIM_GOT_ITEM:
        instructions = (
            "Please describe the item and any details that can help verify it "
            "belongs to you (for example, a mark, colour, contents, or other "
            "detail not mentioned in the announcement). The poster will review "
            "your message and decide whether to share their contact."
        )
    else:
        instructions = (
            "Please describe the item and include details that help the poster "
            "verify it belongs to you. The poster must approve before their "
            "contact details are shared with you."
        )

    await query.message.reply_text(
        f"Report #{report_id}: {report['description']}\n\n{instructions}",
        reply_markup=ReplyKeyboardRemove(),
    )
    await query.answer()


def describe_response(kind: str, claim_type: str) -> str:
    if kind == KIND_LOST:
        return "This person says they found your lost item"
    if claim_type == CLAIM_GOT_ITEM:
        return "I got my lost item"
    return "I wanna contact the person who posted this"


async def notify_poster_of_claim(
    context, report: sqlite3.Row, claim_id: int, claimant, claim_type: str, detail: str
) -> None:
    """Send the claim to the poster, or tell the claimant if that fails."""
    kind = report_kind(report)
    message = (
        f"A person responded to your #{kind} report #{report['id']}.\n\n"
        f"Item: {report['description']}\n"
        f"Their response: {describe_response(kind, claim_type)}\n\n"
        f"Responder: {telegram_username(claimant) or 'No Telegram username'}\n"
        f"Details they provided:\n{detail}\n\n"
        "Approve only if you are comfortable sharing your contact details. "
        "Your contact will not be shared unless you approve below."
    )

    try:
        sent = await context.bot.send_message(
            chat_id=report["user_id"],
            text=message,
            reply_markup=claim_decision_keyboard(claim_id),
        )
    except Forbidden:
        logger.warning(
            "Cannot notify report poster %s about claim %s", report["user_id"], claim_id
        )
        failure = "Telegram would not let the bot notify the poster."
    except TelegramError:
        logger.exception("Failed to notify report poster about claim")
        failure = "I could not notify the poster."
    else:
        set_claim_poster_message(claim_id, sent.message_id)
        return

    set_claim_status(claim_id, CLAIM_NOTIFICATION_FAILED)
    await safe_send_message(
        context,
        claimant.id,
        f"I saved your claim, but {failure} Please {CONTACT_ADMIN_HINT}",
    )


async def receive_claim_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store the claimant's verification text and forward it to the poster."""
    message = update.effective_message
    claimant = update.effective_user
    pending: Optional[PendingClaim] = context.user_data.get(CLAIM_KEY)
    if not pending:
        return

    detail = (message.text or "").strip()
    if not detail:
        await message.reply_text("Please send the item details as text.")
        return

    report = fetch_posted_report(pending.report_id)
    if not report:
        context.user_data.pop(CLAIM_KEY, None)
        await message.reply_text(
            "Sorry, that announcement is no longer available.",
            reply_markup=main_keyboard(),
        )
        return

    claim_id = save_claim(pending.report_id, claimant, detail, pending.claim_type)
    context.user_data.pop(CLAIM_KEY, None)

    await message.reply_text(
        "Thank you. Your details have been sent privately to the person who "
        "posted the report. They must approve before their contact is shared.\n\n"
        "If the item still cannot be returned or you cannot contact the poster, "
        f"{CONTACT_ADMIN_HINT}",
        reply_markup=main_keyboard(),
    )

    await notify_poster_of_claim(
        context, report, claim_id, claimant, pending.claim_type, detail
    )

    await notify_moderators(
        context,
        f"New claim #{claim_id} for report #{pending.report_id}.\n"
        f"Claimant ID: {claimant.id}\nDetails: {detail}",
    )


def build_contact_label(report: sqlite3.Row) -> str:
    """Describe how the claimant can reach the poster after approval."""
    if report["contact_value"]:
        prefix = "Phone" if report["contact_type"] == CONTACT_PHONE else "Contact"
        return f"{prefix}: {report['contact_value']}"
    if report["username"]:
        return f"Telegram: {report['username']}"
    return "The poster approved, but no direct contact detail was saved in the report."


async def on_claim_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The poster approves or declines sharing their contact."""
    query = update.callback_query
    claim_id = int(context.match.group(1))
    approved = context.match.group(2) == "approve"

    claim, report = fetch_claim_with_report(claim_id)

    if not claim:
        await query.answer("This claim no longer exists.", show_alert=True)
        return
    if not report:
        await query.answer("The original report no longer exists.", show_alert=True)
        return
    if query.from_user.id != report["user_id"]:
        await query.answer(
            "Only the person who posted this report can decide.", show_alert=True
        )
        return
    if claim["status"] != CLAIM_PENDING:
        await query.answer("This claim has already been handled.", show_alert=True)
        return

    set_claim_status(claim_id, CLAIM_APPROVED if approved else CLAIM_DECLINED)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass  # The buttons were already removed; nothing to do.

    if not approved:
        await query.answer("Claim declined.")
        await query.message.reply_text(
            f"You declined contact sharing for claim #{claim_id}."
        )
        await safe_send_message(
            context,
            claim["claimant_id"],
            "The person who posted the report declined to share their contact details. "
            f"If you still need help recovering the item, {CONTACT_ADMIN_HINT}",
        )
        return

    await query.answer("Contact sharing approved.")
    await query.message.reply_text(f"You approved contact sharing for claim #{claim_id}.")
    await safe_send_message(
        context,
        claim["claimant_id"],
        (
            f"Good news! The person who posted #{report_kind(report)} report "
            f"#{claim['report_id']} approved your request.\n\n"
            f"{build_contact_label(report)}\n\n"
            "Please contact them to arrange returning the item. If the item still "
            f"cannot be returned or you cannot reach the poster, {CONTACT_ADMIN_HINT}"
        ),
    )


async def on_unknown_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Unknown button.")


# ---------------------------------------------------------
# Error handling
# ---------------------------------------------------------


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("An error occurred while handling an update:", exc_info=context.error)


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

# (regex pattern for callback_data, handler). The first match wins.
CALLBACK_ROUTES = [
    (f"^{CB_CANCEL}$", on_cancel_button),
    (f"^{CB_SKIP_PHOTO}$", on_skip_photo),
    (rf"^{CB_LOCATION}:(\d+)$", on_location_chosen),
    (rf"^{CB_HANDOFF}:(\d+)$", on_handoff_chosen),
    (
        f"^{CB_CONTACT}:({CONTACT_TELEGRAM}|{CONTACT_PHONE}|{CONTACT_BOT})$",
        on_contact_chosen,
    ),
    (f"^{CB_PUBLISH}$", on_publish),
    (rf"^{CB_CLAIM}:(\d+)$", on_claim_button),
    (rf"^{CB_LOST_RESPONSE}:(\d+)$", on_lost_response_button),
    (
        rf"^{CB_CLAIM_CHOICE}:(\d+):({CLAIM_GOT_ITEM}|{CLAIM_CONTACT_POSTER})$",
        on_claim_choice,
    ),
    (rf"^{CB_CLAIM_DECISION}:(\d+):(approve|decline)$", on_claim_decision),
    (
        rf"^{CB_MODERATE}:({MOD_APPROVE}|{MOD_REJECT}|{MOD_RETRY}):(\d+)$",
        on_moderation_button,
    ),
]


def main() -> None:
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("found", start_found))
    app.add_handler(CommandHandler("lost", start_lost))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("pending", pending_command))

    for pattern, callback in CALLBACK_ROUTES:
        app.add_handler(CallbackQueryHandler(callback, pattern=pattern))
    app.add_handler(CallbackQueryHandler(on_unknown_button))  # Fallback.

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)

    # Every minute: publish approved reports that are due, and remind
    # moderators about reports close to their two-hour deadline.
    if app.job_queue is None:
        raise RuntimeError(
            "The scheduler is not installed. Run: python -m pip install -r requirements.txt"
        )
    app.job_queue.run_repeating(
        run_schedule, interval=SCHEDULER_INTERVAL_SECONDS, first=5, name="schedule"
    )

    if not MODERATOR_IDS:
        logger.warning(
            "ADMIN_USER_IDS is empty: reports are approved automatically "
            "(the publishing schedule still applies)."
        )

    print("Lost & Found bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
