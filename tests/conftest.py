"""Shared fixtures and fake Telegram objects for the bot tests.

Nothing here talks to Telegram: the bot object, messages and callback
queries are small fakes that record what the handlers do.
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# bot.py reads its configuration at import time. Set every value explicitly
# so a developer's real .env (load_dotenv never overrides) cannot leak in.
os.environ.update(
    {
        "BOT_TOKEN": "123456:TEST-TOKEN",
        "CHANNEL_ID": "@lost_found_test",
        "ADMIN_USER_IDS": "",
        "ADMIN_USER_ID": "",
        "TIMEZONE": "Asia/Tashkent",
        "PUBLISH_START_HOUR": "9",
        "PUBLISH_END_HOUR": "22",
    }
)

import bot  # noqa: E402

MOD_ID = 999
REAL_NOW_LOCAL = bot.now_local  # saved before the clock fixture patches it
TZ = bot.TIMEZONE


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeBot:
    """Records send_message / send_photo calls; can be told to fail."""

    def __init__(self):
        self.calls = []
        self.fail = None  # callable(method, kwargs) -> Exception | None
        self._next_id = 1000

    async def _record(self, method, kwargs):
        self.calls.append(SimpleNamespace(method=method, **kwargs))
        if self.fail:
            error = self.fail(method, kwargs)
            if error:
                raise error
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    async def send_message(self, **kwargs):
        return await self._record("send_message", kwargs)

    async def send_photo(self, **kwargs):
        return await self._record("send_photo", kwargs)

    def to(self, chat_id):
        return [c for c in self.calls if c.chat_id == chat_id]

    def texts_to(self, chat_id):
        return [getattr(c, "text", None) or getattr(c, "caption", None) or "" for c in self.to(chat_id)]

    def fail_for(self, chat_id, error, method=None):
        def rule(m, kwargs):
            if kwargs.get("chat_id") == chat_id and (method is None or m == method):
                return error
            return None

        self.fail = rule


class FakeMessage:
    def __init__(self, text=None, photo=None, chat_id=1):
        self.text = text
        self.photo = photo or []
        self.chat_id = chat_id
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(SimpleNamespace(text=text, **kwargs))
        return SimpleNamespace(message_id=1)

    @property
    def last(self):
        return self.replies[-1].text if self.replies else None


class FakeQuery:
    def __init__(self, data, user, message):
        self.data = data
        self.from_user = user
        self.message = message
        self.answers = []
        self.edited = []
        self.edit_error = None

    async def answer(self, text=None, show_alert=False):
        self.answers.append(SimpleNamespace(text=text, show_alert=show_alert))

    async def edit_message_reply_markup(self, reply_markup=None):
        if self.edit_error:
            raise self.edit_error
        self.edited.append(reply_markup)


def make_user(user_id, username="user"):
    return SimpleNamespace(id=user_id, username=username, full_name=f"User {user_id}")


class Harness:
    """Drives handlers the way python-telegram-bot would, per user."""

    def __init__(self):
        self.bot = FakeBot()
        self.user_data = defaultdict(dict)

    def context(self, user, match=None):
        return SimpleNamespace(
            bot=self.bot, user_data=self.user_data[user.id], match=match, error=None
        )

    def flow(self, user):
        return self.user_data[user.id].get(bot.FLOW_KEY)

    async def text(self, user, text):
        message = FakeMessage(text=text, chat_id=user.id)
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
            effective_chat=SimpleNamespace(id=user.id),
            callback_query=None,
        )
        await bot.handle_text(update, self.context(user))
        return message

    async def command(self, user, handler):
        message = FakeMessage(text="/cmd", chat_id=user.id)
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
            effective_chat=SimpleNamespace(id=user.id),
            callback_query=None,
        )
        await handler(update, self.context(user))
        return message

    async def photo(self, user, file_id="PHOTO-1"):
        message = FakeMessage(
            photo=[SimpleNamespace(file_id="thumb"), SimpleNamespace(file_id=file_id)],
            chat_id=user.id,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
            effective_chat=SimpleNamespace(id=user.id),
            callback_query=None,
        )
        await bot.handle_photo(update, self.context(user))
        return message

    async def press(self, user, data, chat_id=None, edit_error=None):
        """Press an inline button; routed exactly like main() registers them."""
        message = FakeMessage(chat_id=chat_id or user.id)
        query = FakeQuery(data, user, message)
        query.edit_error = edit_error
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
            effective_chat=SimpleNamespace(id=message.chat_id),
            callback_query=query,
        )
        handler, match = route(data)
        await handler(update, self.context(user, match))
        return query


def route(data):
    for pattern, handler in bot.CALLBACK_ROUTES:
        match = re.match(pattern, data)
        if match:
            return handler, match
    return bot.on_unknown_button, None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)

    def set(self, *args):
        self.now = datetime(*args, tzinfo=TZ)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Freeze the bot's local clock at Fri 20.11.2026 12:00 (inside the window)."""
    c = Clock()
    monkeypatch.setattr(bot, "now_local", lambda: c.now)
    return c


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(bot, "DATABASE_FILE", str(path))
    bot.init_db()
    return path


@pytest.fixture(autouse=True)
def moderators(monkeypatch):
    monkeypatch.setattr(bot, "MODERATOR_IDS", frozenset({MOD_ID}))


@pytest.fixture
def no_moderators(monkeypatch):
    monkeypatch.setattr(bot, "MODERATOR_IDS", frozenset())


@pytest.fixture
def h():
    return Harness()


@pytest.fixture
def alice():
    return make_user(1, "alice")


@pytest.fixture
def bob():
    return make_user(2, "bob")


@pytest.fixture
def mod():
    return make_user(MOD_ID, "moderator")


def make_flow(kind=bot.KIND_FOUND, **overrides):
    values = dict(
        kind=kind,
        step=bot.Step.PREVIEW,
        user_id=1,
        username="@alice",
        photo_file_id=None,
        description="Black wallet",
        found_place="Library",
        found_detail="2nd floor near window",
        handoff="Student affairs" if kind == bot.KIND_FOUND else "",
        handoff_detail="Room 101" if kind == bot.KIND_FOUND else "",
        contact_type=bot.CONTACT_TELEGRAM,
        contact_value="@alice",
    )
    values.update(overrides)
    return bot.ReportFlow(**values)


def save(flow=None, user=None, status=None, **flow_overrides):
    """Save a report directly; optionally force its status."""
    flow = flow or make_flow(**flow_overrides)
    user = user or make_user(flow.user_id, (flow.username or "").lstrip("@") or None)
    report_id = bot.save_report(flow, user, "2026-11-20T12:00:00")
    if status:
        with bot.db_session() as conn:
            conn.execute("UPDATE reports SET status = ? WHERE id = ?", (status, report_id))
    return report_id
