"""Pure functions: configuration, scheduling maths, formatting, keyboards."""

import re
from datetime import datetime, timezone

import pytest

import bot
from tests.conftest import TZ, make_flow, make_user, route


# --- configuration ---------------------------------------------------------


def test_require_env_returns_value(monkeypatch):
    monkeypatch.setenv("SOME_VAR", "x")
    assert bot.require_env("SOME_VAR") == "x"


@pytest.mark.parametrize("value", [None, ""])
def test_require_env_missing_raises(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SOME_VAR", raising=False)
    else:
        monkeypatch.setenv("SOME_VAR", value)
    with pytest.raises(RuntimeError, match="SOME_VAR is missing"):
        bot.require_env("SOME_VAR")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", frozenset()),
        ("111", frozenset({111})),
        ("111, 222", frozenset({111, 222})),
        ("111;222;, ,", frozenset({111, 222})),
    ],
)
def test_parse_user_ids(monkeypatch, raw, expected):
    monkeypatch.setenv("IDS", raw)
    assert bot.parse_user_ids("IDS") == expected


def test_parse_user_ids_unset(monkeypatch):
    monkeypatch.delenv("IDS", raising=False)
    assert bot.parse_user_ids("IDS") == frozenset()


def test_parse_user_ids_invalid(monkeypatch):
    monkeypatch.setenv("IDS", "111,abc")
    with pytest.raises(RuntimeError, match="invalid Telegram user ID: 'abc'"):
        bot.parse_user_ids("IDS")


# --- small helpers ---------------------------------------------------------


def test_step_label():
    assert bot.step_label(make_flow(), bot.Step.HANDOFF) == "Step 4 of 6"
    assert bot.step_label(make_flow(bot.KIND_LOST), bot.Step.PHOTO) == "Step 3 of 4"


def test_telegram_username():
    assert bot.telegram_username(make_user(1, "alice")) == "@alice"
    assert bot.telegram_username(make_user(1, None)) is None


def test_report_flow_is_lost():
    assert make_flow(bot.KIND_LOST).is_lost
    assert not make_flow(bot.KIND_FOUND).is_lost


def test_is_moderator():
    assert bot.is_moderator(make_user(999))
    assert not bot.is_moderator(make_user(1))
    assert not bot.is_moderator(None)


def test_report_kind_defaults_to_found():
    assert bot.report_kind({"kind": None}) == "FOUND"
    assert bot.report_kind({"kind": "lost"}) == "LOST"


def test_clear_session_removes_all_keys():
    ctx = type("C", (), {})()
    ctx.user_data = {bot.FLOW_KEY: 1, bot.CLAIM_KEY: 2, bot.REJECT_KEY: 3, "other": 4}
    bot.clear_session(ctx)
    assert ctx.user_data == {"other": 4}


# --- publishing schedule ---------------------------------------------------


def at(h, m=0, day=20):
    return datetime(2026, 11, day, h, m, tzinfo=TZ)


@pytest.mark.parametrize(
    "moment, inside",
    [(at(8, 59), False), (at(9, 0), True), (at(21, 59), True), (at(22, 0), False), (at(0, 0), False)],
)
def test_is_within_publish_hours(moment, inside):
    assert bot.is_within_publish_hours(moment) is inside


def test_is_within_publish_hours_defaults_to_now(clock):
    clock.set(2026, 11, 20, 23, 0)
    assert bot.is_within_publish_hours() is False


def test_compute_publish_after_inside_window_is_immediate():
    assert bot.compute_publish_after(at(14, 37)) == at(14, 37)


def test_compute_publish_after_early_morning_waits_until_nine():
    assert bot.compute_publish_after(at(6, 15)) == at(9, 0)


def test_compute_publish_after_late_evening_waits_until_next_morning():
    assert bot.compute_publish_after(at(22, 30)) == at(9, 0, day=21)


def test_compute_publish_after_converts_from_utc():
    # 18:00 UTC is 23:00 in Tashkent (UTC+5) -> next day 09:00 local.
    utc = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
    result = bot.compute_publish_after(utc)
    assert result == at(9, 0, day=21)
    assert result.tzinfo == TZ


def test_compute_publish_after_defaults_to_now(clock):
    clock.set(2026, 11, 20, 7, 0)
    assert bot.compute_publish_after() == at(9, 0)


def test_db_time_round_trip_is_utc():
    stored = bot.to_db_time(at(14, 0))
    assert stored == "2026-11-20T09:00:00+00:00"
    assert bot.from_db_time(stored) == at(14, 0)


def test_from_db_time_naive_is_treated_as_utc():
    assert bot.from_db_time("2026-11-20T09:00:00") == at(14, 0)


@pytest.mark.parametrize("value", [None, "", "not a date"])
def test_from_db_time_invalid(value):
    assert bot.from_db_time(value) is None


def test_format_local():
    assert bot.format_local(None) == "unknown"
    assert bot.format_local(datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)) == "20.11.2026 14:00"


def test_publish_window_text():
    assert bot.publish_window_text() == "09:00–22:00"


# --- announcement formatting ------------------------------------------------


def test_format_place_other_shows_only_detail():
    flow = make_flow(found_place=bot.OTHER_LOCATION, found_detail="  Gym lockers ")
    assert bot.format_place(flow) == "Gym lockers"


def test_format_place_avoids_duplicate_prefix():
    flow = make_flow(found_place="MATH", found_detail="math 206")
    assert bot.format_place(flow) == "math 206"


def test_format_place_normal():
    assert bot.format_place(make_flow()) == "Library — 2nd floor near window"


def test_format_handoff_variants():
    assert bot.format_handoff(make_flow()) == "Student affairs — Room 101"
    assert bot.format_handoff(make_flow(handoff=bot.HANDOFF_LEFT_IT, handoff_detail="")) == bot.HANDOFF_LEFT_IT
    assert bot.format_handoff(make_flow(handoff=bot.OTHER_HANDOFF, handoff_detail=" TA Ali ")) == "TA Ali"
    assert bot.format_handoff(make_flow(handoff="Library staff", handoff_detail=None)) == "Library staff"


def test_format_contact_variants():
    assert bot.format_contact(make_flow()) == "@alice"
    assert bot.format_contact(make_flow(contact_type=bot.CONTACT_PHONE, contact_value="+998 90 000")) == "+998 90 000"
    assert bot.format_contact(make_flow(contact_value=None)) == "Contact details were not provided."
    assert "I think this is mine" in bot.format_contact(make_flow(contact_type=bot.CONTACT_BOT))
    assert "I found this item" in bot.format_contact(make_flow(bot.KIND_LOST, contact_type=bot.CONTACT_BOT))


def test_make_announcement_found():
    text = bot.make_announcement(make_flow(photo_file_id="P"), "2026-11-20T12:34:56")
    assert text.startswith("#FOUND\n\n")
    assert "🔎 Item: Black wallet" in text
    assert "📷 Photo: Attached" in text
    assert "📍 Found at: Library — 2nd floor near window" in text
    assert "📦 Left/handed in: Student affairs — Room 101" in text
    assert "📅 Reported: 2026-11-20 12:34" in text


def test_make_announcement_lost_without_photo():
    text = bot.make_announcement(make_flow(bot.KIND_LOST), "2026-11-20T08:00:00")
    assert text.startswith("#LOST\n\n")
    assert "Probably lost at" in text
    assert "Photo" not in text
    assert "Left/handed in" not in text


def test_make_announcement_preview_uses_current_time():
    text = bot.make_announcement(make_flow())
    assert re.search(r"📅 Reported: \d{4}-\d\d-\d\d \d\d:\d\d$", text)


def test_build_announcement_truncates_very_long_text():
    from tests.conftest import save

    report_id = save(description="x" * 5000)
    text = bot.build_announcement(bot.fetch_report(report_id))
    assert len(text) <= 4096
    assert text.endswith("…(announcement shortened)")


def test_describe_response():
    assert "found your lost item" in bot.describe_response(bot.KIND_LOST, bot.CLAIM_FOUND_ITEM)
    assert bot.describe_response(bot.KIND_FOUND, bot.CLAIM_GOT_ITEM) == "I got my lost item"
    assert "contact the person" in bot.describe_response(bot.KIND_FOUND, bot.CLAIM_CONTACT_POSTER)


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"contact_value": "+998", "contact_type": "phone", "username": "@a"}, "Phone: +998"),
        ({"contact_value": "@a", "contact_type": "telegram", "username": "@a"}, "Contact: @a"),
        ({"contact_value": None, "contact_type": "bot", "username": "@a"}, "Telegram: @a"),
        ({"contact_value": None, "contact_type": "bot", "username": None}, "no direct contact detail"),
    ],
)
def test_build_contact_label(row, expected):
    assert expected in bot.build_contact_label(row)


def test_build_location_detail_prompt_variants():
    found, lost = make_flow(), make_flow(bot.KIND_LOST)
    assert bot.build_location_detail_prompt(found, "UCA").startswith("You chose UCA.")
    assert bot.build_location_detail_prompt(found, bot.OTHER_LOCATION).startswith("Please specify the exact place")
    assert "lost it" in bot.build_location_detail_prompt(lost, "UCA")
    assert bot.build_location_detail_prompt(lost, bot.OTHER_LOCATION).startswith("Where do you think")


# --- keyboards ---------------------------------------------------------------


def all_buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def test_location_keyboard_layout():
    markup = bot.location_keyboard()
    rows = markup.inline_keyboard
    assert all(len(row) <= 2 for row in rows[:-1])
    assert rows[-1][0].callback_data == bot.CB_CANCEL
    labels = [b.text for b in all_buttons(markup)]
    assert bot.OTHER_LOCATION_LABEL in labels
    assert bot.OTHER_LOCATION not in labels
    assert len(labels) == len(bot.FOUND_LOCATIONS) + 1


def test_main_keyboard_has_found_and_lost():
    markup = bot.main_keyboard()
    assert [b.text for b in markup.keyboard[0]] == [bot.FOUND_BUTTON, bot.LOST_BUTTON]


KEYBOARDS = [
    bot.photo_keyboard(),
    bot.location_keyboard(),
    bot.handoff_keyboard(),
    bot.contact_keyboard(),
    bot.contact_fallback_keyboard(),
    bot.preview_keyboard(),
    bot.moderation_keyboard(7),
    bot.retry_keyboard(7),
    bot.claim_keyboard(7),
    bot.lost_response_keyboard(7),
    bot.claim_choice_keyboard(7),
    bot.claim_decision_keyboard(7),
]


@pytest.mark.parametrize("markup", KEYBOARDS)
def test_every_button_is_routed_to_a_real_handler(markup):
    """Each callback_data the bot emits must match a CALLBACK_ROUTES pattern."""
    for button in all_buttons(markup):
        handler, _ = route(button.callback_data)
        assert handler is not bot.on_unknown_button, button.callback_data
        assert len(button.callback_data.encode()) <= 64  # Telegram limit


def test_unknown_callback_falls_back():
    handler, match = route("something:else")
    assert handler is bot.on_unknown_button and match is None


# --- startup & real clock ------------------------------------------------------


def test_real_now_local_is_in_configured_timezone():
    from tests.conftest import REAL_NOW_LOCAL

    assert REAL_NOW_LOCAL().tzinfo is TZ


def test_unknown_timezone_fails_with_helpful_message(monkeypatch):
    import importlib.util

    monkeypatch.setenv("TIMEZONE", "Not/A_Zone")
    spec = importlib.util.spec_from_file_location("bot_bad_tz", bot.__file__)
    with pytest.raises(RuntimeError, match="Time zone data is missing"):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
