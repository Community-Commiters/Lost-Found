"""End-to-end conversation for FOUND and LOST reports, plus edge cases."""

import sqlite3

from telegram import ReplyKeyboardRemove
from telegram.error import TelegramError

import bot
from tests.conftest import MOD_ID, make_user

Step = bot.Step


def loc(name):
    return f"location:{bot.FOUND_LOCATIONS.index(name)}"


def handoff(name):
    return f"handoff:{bot.HANDOFF_OPTIONS.index(name)}"


# --- /start and /cancel ---------------------------------------------------------


async def test_start_shows_main_menu(h, alice):
    msg = await h.command(alice, bot.start)
    assert "Welcome" in msg.last
    assert msg.replies[-1].reply_markup.keyboard[0][0].text == bot.FOUND_BUTTON


async def test_start_during_active_report_does_not_reset_it(h, alice):
    await h.command(alice, bot.start_found)
    msg = await h.command(alice, bot.start)
    assert "still active" in msg.last
    assert h.flow(alice) is not None


async def test_cancel_command_clears_session(h, alice):
    await h.command(alice, bot.start_found)
    h.user_data[alice.id][bot.CLAIM_KEY] = "x"
    msg = await h.command(alice, bot.cancel)
    assert msg.last == bot.CANCELLED_TEXT
    assert h.user_data[alice.id] == {}


async def test_cancel_button_clears_session(h, alice):
    await h.command(alice, bot.start_lost)
    query = await h.press(alice, bot.CB_CANCEL)
    assert query.answers and query.message.last == bot.CANCELLED_TEXT
    assert h.flow(alice) is None


# --- happy paths ------------------------------------------------------------------


async def test_full_found_flow_submits_for_review(h, alice):
    await h.text(alice, bot.FOUND_BUTTON)
    first = h.bot.to(alice.id)[0]
    assert "Starting your FOUND report" in first.text
    assert isinstance(first.reply_markup, ReplyKeyboardRemove)
    assert "Step 1 of 6" in h.bot.texts_to(alice.id)[-1]

    msg = await h.photo(alice, "BIG-PHOTO")
    assert msg.last == "Photo attached successfully."
    assert h.flow(alice).photo_file_id == "BIG-PHOTO"
    assert h.flow(alice).step is Step.DESCRIPTION

    await h.text(alice, "  Black wallet  ")
    assert h.flow(alice).description == "Black wallet"
    assert h.flow(alice).step is Step.LOCATION

    q = await h.press(alice, loc("UCA"))
    assert q.message.last.startswith("You chose UCA.")
    await h.text(alice, "UCA 204 near window")
    assert h.flow(alice).found_detail == "UCA 204 near window"
    assert h.flow(alice).step is Step.HANDOFF

    q = await h.press(alice, handoff("Student affairs"))
    assert q.message.last.startswith("You selected Student affairs.")
    await h.text(alice, "Room 101")
    assert h.flow(alice).step is Step.CONTACT

    await h.press(alice, "contact:telegram")
    flow = h.flow(alice)
    assert (flow.contact_type, flow.contact_value) == ("telegram", "@alice")
    assert flow.step is Step.PREVIEW
    preview = h.bot.texts_to(alice.id)[-1]
    assert "Step 6 of 6 — Preview your FOUND announcement" in preview
    assert "📍 Found at: UCA 204 near window" in preview  # no "UCA — UCA 204"
    assert "📦 Left/handed in: Student affairs — Room 101" in preview

    q = await h.press(alice, bot.CB_PUBLISH)
    assert h.flow(alice) is None
    assert q.edited == [None]  # Submit button removed
    assert "report #1 was sent to the moderators" in q.message.last

    row = bot.fetch_report(1)
    assert row["status"] == bot.REPORT_PENDING_REVIEW
    assert row["kind"] == "FOUND" and row["photo_file_id"] == "BIG-PHOTO"

    review = h.bot.to(MOD_ID)
    assert len(review) == 1 and review[0].method == "send_photo"
    assert "#FOUND report #1 — waiting for review" in review[0].caption
    assert "published immediately" in review[0].caption
    assert review[0].reply_markup.inline_keyboard[0][0].callback_data == "mod:approve:1"


async def test_full_lost_flow_with_other_location_and_phone(h, alice):
    await h.text(alice, "lost")  # plain text works as well as the button
    assert h.flow(alice).kind == bot.KIND_LOST
    assert "Step 1 of 4" in h.bot.texts_to(alice.id)[-1]

    await h.text(alice, "Blue umbrella")
    q = await h.press(alice, loc(bot.OTHER_LOCATION))
    assert q.message.last.startswith("Where do you think you lost it?")
    await h.text(alice, "Bus stop")
    assert h.flow(alice).step is Step.PHOTO
    assert "attach a photo of the lost item" in h.bot.texts_to(alice.id)[-1]

    await h.press(alice, bot.CB_SKIP_PHOTO)
    assert h.flow(alice).step is Step.CONTACT

    q = await h.press(alice, "contact:phone")
    assert "the person who finds your item" in q.message.last
    msg = await h.text(alice, "123")
    assert "too short" in msg.last
    assert h.flow(alice).step is Step.PHONE
    await h.text(alice, "+998 90 123 45 67")
    preview = h.bot.texts_to(alice.id)[-1]
    assert "#LOST" in preview and "📍 Probably lost at: Bus stop" in preview
    assert "📞 Contact: +998 90 123 45 67" in preview

    await h.press(alice, bot.CB_PUBLISH)
    row = bot.fetch_report(1)
    assert row["kind"] == "LOST" and row["contact_type"] == "phone"


async def test_handoff_without_detail_goes_straight_to_contact(h, alice):
    await h.command(alice, bot.start_found)
    await h.press(alice, bot.CB_SKIP_PHOTO)
    await h.text(alice, "Keys")
    await h.press(alice, loc("Library"))
    await h.text(alice, "desk 3")
    await h.press(alice, handoff(bot.HANDOFF_LEFT_IT))
    assert h.flow(alice).step is Step.CONTACT
    assert h.flow(alice).handoff_detail == ""


async def test_other_handoff_asks_for_free_text(h, alice):
    await h.command(alice, bot.start_found)
    h.flow(alice).step = Step.HANDOFF
    q = await h.press(alice, handoff(bot.OTHER_HANDOFF))
    assert q.message.last == bot.HANDOFF_DETAIL_PROMPT
    assert h.flow(alice).step is Step.HANDOFF_DETAIL


async def test_contact_without_username_falls_back(h):
    nameless = make_user(5, None)
    await h.command(nameless, bot.start_found)
    h.flow(nameless).step = Step.CONTACT
    q = await h.press(nameless, "contact:telegram")
    assert "don't have a Telegram username" in q.message.last
    assert h.flow(nameless).step is Step.CONTACT_FALLBACK

    msg = await h.text(nameless, "hello?")
    assert "choose a contact option" in msg.last

    await h.press(nameless, "contact:bot")
    flow = h.flow(nameless)
    assert (flow.contact_type, flow.contact_value, flow.step) == ("bot", None, Step.PREVIEW)
    assert "Use the “I think this is mine” button below." in h.bot.texts_to(5)[-1]


async def test_found_phone_prompt_mentions_owner(h, alice):
    await h.command(alice, bot.start_found)
    q = await h.press(alice, "contact:phone")
    assert "share with the owner" in q.message.last


# --- input validation & out-of-order messages ---------------------------------------


async def test_text_without_active_report_shows_menu(h, alice):
    msg = await h.text(alice, "hello")
    assert "Choose FOUND or LOST" in msg.last


async def test_text_at_photo_step_asks_for_photo(h, alice):
    await h.command(alice, bot.start_found)
    msg = await h.text(alice, "here is text")
    assert "Please send a photo" in msg.last


async def test_empty_answers_are_rejected(h, alice):
    await h.command(alice, bot.start_lost)
    msg = await h.text(alice, "   ")
    assert "short description" in msg.last
    h.flow(alice).step = Step.LOCATION_DETAIL
    msg = await h.text(alice, "")
    assert "exact place" in msg.last
    h.flow(alice).step = Step.HANDOFF_DETAIL
    msg = await h.text(alice, " ")
    assert "details of the place or person" in msg.last


async def test_text_at_preview_and_button_only_steps(h, alice):
    await h.command(alice, bot.start_found)
    h.flow(alice).step = Step.PREVIEW
    msg = await h.text(alice, "ok")
    assert "Your report is ready" in msg.last
    h.flow(alice).step = Step.LOCATION
    msg = await h.text(alice, "Library")
    assert "Please use the buttons" in msg.last


async def test_photo_without_report_or_at_wrong_step(h, alice):
    msg = await h.photo(alice)
    assert "start with /found or /lost" in msg.last
    await h.command(alice, bot.start_lost)  # LOST starts at DESCRIPTION
    msg = await h.photo(alice)
    assert "start with /found or /lost" in msg.last


async def test_skip_photo_without_photo_step(h, alice):
    q = await h.press(alice, bot.CB_SKIP_PHOTO)
    assert "no active photo step" in q.message.last


async def test_buttons_after_session_expired(h, alice):
    for data in (loc("Library"), handoff("Student affairs"), "contact:bot"):
        q = await h.press(alice, data)
        assert q.message.last == bot.SESSION_EXPIRED_TEXT


async def test_out_of_range_indexes_are_rejected(h, alice):
    await h.command(alice, bot.start_found)
    q = await h.press(alice, "location:99")
    assert q.message.last == "Invalid location selection."
    q = await h.press(alice, "handoff:99")
    assert q.message.last == "Invalid selection."


# --- submitting -------------------------------------------------------------------


async def ready_to_submit(h, user, kind=bot.KIND_FOUND):
    await h.command(user, bot.start_found if kind == bot.KIND_FOUND else bot.start_lost)
    flow = h.flow(user)
    flow.description, flow.found_place, flow.found_detail = "Phone", "Library", "desk"
    flow.handoff, flow.contact_type = bot.HANDOFF_LEFT_IT, bot.CONTACT_BOT
    flow.step = Step.PREVIEW
    return flow


async def test_publish_without_ready_report(h, alice):
    q = await h.press(alice, bot.CB_PUBLISH)
    assert "no ready-to-submit report" in q.message.last


async def test_double_tap_submit_creates_one_report(h, alice):
    flow = await ready_to_submit(h, alice)
    flow.step = Step.SUBMITTING  # first tap still saving
    q = await h.press(alice, bot.CB_PUBLISH)
    assert "no ready-to-submit report" in q.message.last
    assert bot.fetch_report_ids("1=1") == []


async def test_submit_database_error_keeps_answers(h, alice, monkeypatch):
    await ready_to_submit(h, alice)

    def broken(*args):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(bot, "save_report", broken)
    q = await h.press(alice, bot.CB_PUBLISH)
    assert "couldn't save this report" in q.message.last
    assert "disk I/O error" in q.message.last
    assert h.flow(alice).step is Step.PREVIEW


async def test_submit_tolerates_button_removal_error(h, alice):
    await ready_to_submit(h, alice)
    q = await h.press(alice, bot.CB_PUBLISH, edit_error=TelegramError("gone"))
    assert "sent to the moderators" in q.message.last


async def test_submit_without_moderators_publishes_inside_window(h, alice, no_moderators):
    await ready_to_submit(h, alice)
    q = await h.press(alice, bot.CB_PUBLISH)
    assert "has been accepted" in q.message.last
    row = bot.fetch_report(1)
    assert row["status"] == bot.REPORT_POSTED and row["moderated_by"] is None
    channel = h.bot.to(bot.CHANNEL_ID)
    assert len(channel) == 1 and channel[0].text.startswith("#FOUND")
    assert "now published" in h.bot.texts_to(alice.id)[-1]


async def test_submit_without_moderators_at_night_is_scheduled(h, alice, no_moderators, clock):
    clock.set(2026, 11, 20, 23, 30)
    await ready_to_submit(h, alice, bot.KIND_LOST)
    await h.press(alice, bot.CB_PUBLISH)
    assert bot.fetch_report(1)["status"] == bot.REPORT_SCHEDULED
    assert h.bot.to(bot.CHANNEL_ID) == []
    assert "published in the channel at 21.11.2026 09:00" in h.bot.texts_to(alice.id)[-1]


async def test_unexpected_contact_choice_is_ignored(h, alice):
    """The router only allows telegram|phone|bot; anything else changes nothing."""
    import re
    from types import SimpleNamespace

    from tests.conftest import FakeMessage, FakeQuery

    await h.command(alice, bot.start_found)
    message = FakeMessage(chat_id=alice.id)
    query = FakeQuery("contact:fax", alice, message)
    update = SimpleNamespace(effective_message=message, callback_query=query)
    await bot.on_contact_chosen(update, h.context(alice, re.match(r"contact:(\w+)", "contact:fax")))
    assert h.flow(alice).step is Step.PHOTO and message.replies == []
