"""Moderator review, rejection, retry, /pending and the publishing scheduler."""

from types import SimpleNamespace

from telegram.error import TelegramError

import bot
from tests.conftest import MOD_ID, make_user, save

CHANNEL = bot.CHANNEL_ID


def status(report_id):
    return bot.fetch_report(report_id)["status"]


# --- approve -------------------------------------------------------------------


async def test_non_moderator_cannot_moderate(h, bob):
    report_id = save()
    q = await h.press(bob, f"mod:approve:{report_id}")
    assert q.answers[-1].text == "Only moderators can do this." and q.answers[-1].show_alert
    assert status(report_id) == bot.REPORT_PENDING_REVIEW


async def test_approve_inside_window_publishes_immediately(h, mod):
    report_id = save(photo_file_id="PH")
    q = await h.press(mod, f"mod:approve:{report_id}")

    row = bot.fetch_report(report_id)
    assert row["status"] == bot.REPORT_POSTED
    assert row["moderated_by"] == MOD_ID
    post = h.bot.to(CHANNEL)[0]
    assert post.method == "send_photo" and post.caption.startswith("#FOUND")
    assert post.reply_markup.inline_keyboard[0][0].callback_data == f"claim:{report_id}"
    assert row["channel_message_id"] is not None
    assert "now published" in h.bot.texts_to(1)[-1]
    assert q.answers[0].text == "Approved."
    assert "approved by @moderator: published to the channel now" in q.message.last
    assert q.edited == [None]


async def test_approve_at_night_schedules_for_morning(h, mod, clock):
    clock.set(2026, 11, 20, 22, 45)
    report_id = save()
    q = await h.press(mod, f"mod:approve:{report_id}")
    assert status(report_id) == bot.REPORT_SCHEDULED
    assert h.bot.to(CHANNEL) == []
    assert "It will be published in the channel at 21.11.2026 09:00" in h.bot.texts_to(1)[-1]
    assert "scheduled for 21.11.2026 09:00" in q.message.last


async def test_approve_twice_reports_already_handled(h, mod):
    report_id = save()
    await h.press(mod, f"mod:approve:{report_id}")
    q = await h.press(mod, f"mod:approve:{report_id}")
    assert q.answers[-1].text == f"Report #{report_id} was already handled (posted)."
    assert len(h.bot.to(CHANNEL)) == 1


async def test_approve_missing_report(h, mod):
    q = await h.press(mod, "mod:approve:404")
    assert "already handled (deleted)" in q.answers[-1].text


async def test_approve_with_failing_channel_marks_failed(h, mod):
    report_id = save()
    h.bot.fail_for(CHANNEL, TelegramError("Chat not found"))
    q = await h.press(mod, f"mod:approve:{report_id}")
    assert status(report_id) == bot.REPORT_FAILED
    warning = [c for c in h.bot.to(MOD_ID) if "could not be posted" in c.text][0]
    assert "Chat not found" in warning.text
    assert warning.reply_markup.inline_keyboard[0][0].callback_data == f"mod:retry:{report_id}"
    assert "publishing failed" in q.message.last


# --- reject ----------------------------------------------------------------------


async def test_reject_with_reason_notifies_reporter(h, mod):
    report_id = save()
    q = await h.press(mod, f"mod:reject:{report_id}")
    assert "Send the reason" in q.message.last
    assert h.user_data[MOD_ID][bot.REJECT_KEY] == report_id

    msg = await h.text(mod, "Photo is blurry")
    assert msg.last == f"❌ Report #{report_id} rejected."
    row = bot.fetch_report(report_id)
    assert (row["status"], row["reject_reason"]) == (bot.REPORT_REJECTED, "Photo is blurry")
    note = h.bot.texts_to(1)[-1]
    assert "was not approved" in note and "Reason: Photo is blurry" in note
    assert bot.REJECT_KEY not in h.user_data[MOD_ID]


async def test_reject_with_dash_has_no_reason(h, mod):
    report_id = save()
    await h.press(mod, f"mod:reject:{report_id}")
    await h.text(mod, "-")
    assert bot.fetch_report(report_id)["reject_reason"] is None
    assert "Reason:" not in h.bot.texts_to(1)[-1]


async def test_reject_already_handled_report(h, mod):
    report_id = save(status=bot.REPORT_POSTED)
    q = await h.press(mod, f"mod:reject:{report_id}")
    assert "already handled" in q.answers[-1].text
    assert bot.REJECT_KEY not in h.user_data[MOD_ID]


async def test_reject_reason_after_someone_else_handled_it(h, mod):
    report_id = save()
    await h.press(mod, f"mod:reject:{report_id}")
    bot.change_report_status(report_id, bot.REPORT_PENDING_REVIEW, bot.REPORT_SCHEDULED)
    msg = await h.text(mod, "spam")
    assert "already handled by someone else" in msg.last
    assert status(report_id) == bot.REPORT_SCHEDULED


async def test_reject_reason_from_non_moderator_is_ignored(h, bob):
    report_id = save()
    h.user_data[bob.id][bot.REJECT_KEY] = report_id
    msg = await h.text(bob, "nope")
    assert msg.replies == []
    assert status(report_id) == bot.REPORT_PENDING_REVIEW


async def test_reject_when_reporter_unreachable_is_still_rejected(h, mod):
    report_id = save()
    h.bot.fail_for(1, TelegramError("blocked"))
    await h.press(mod, f"mod:reject:{report_id}")
    msg = await h.text(mod, "dup")
    assert "rejected" in msg.last and status(report_id) == bot.REPORT_REJECTED


# --- retry ----------------------------------------------------------------------


async def test_retry_publishes_failed_report(h, mod):
    report_id = save(status=bot.REPORT_FAILED, kind=bot.KIND_LOST)
    q = await h.press(mod, f"mod:retry:{report_id}")
    assert q.answers[0].text == "Retrying…"
    assert q.message.last == f"Report #{report_id} published."
    post = h.bot.to(CHANNEL)[0]
    assert post.reply_markup.inline_keyboard[0][0].callback_data == f"lost_response:{report_id}"


async def test_retry_that_fails_again(h, mod):
    report_id = save(status=bot.REPORT_FAILED)
    h.bot.fail_for(CHANNEL, TelegramError("still broken"))
    q = await h.press(mod, f"mod:retry:{report_id}")
    assert q.message.last == f"Report #{report_id} still could not be published."
    assert status(report_id) == bot.REPORT_FAILED


async def test_retry_on_report_not_failed(h, mod):
    report_id = save(status=bot.REPORT_POSTED)
    q = await h.press(mod, f"mod:retry:{report_id}")
    assert q.answers[-1].text == "This report is not waiting for a retry."


# --- /pending ---------------------------------------------------------------------


async def test_pending_is_moderator_only(h, bob):
    msg = await h.command(bob, bot.pending_command)
    assert msg.last == "This command is for moderators only."


async def test_pending_lists_queue_and_resends_items(h, mod, clock):
    clock.set(2026, 11, 20, 23, 0)
    pending = save()
    scheduled = save(status=bot.REPORT_SCHEDULED)
    failed = save(status=bot.REPORT_FAILED)

    msg = await h.command(mod, bot.pending_command)
    summary = msg.replies[0].text
    assert "Waiting for review: 1" in summary
    assert f"#{scheduled} at 21.11.2026 09:00" in summary
    assert "Failed to publish: 1" in summary
    assert msg.replies[1].reply_markup.inline_keyboard[0][0].callback_data == f"mod:retry:{failed}"
    review = h.bot.to(MOD_ID)[0]
    assert f"report #{pending} — waiting for review" in review.text
    assert "it is published at 21.11.2026 09:00" in review.text


async def test_pending_with_empty_queue(h, mod):
    msg = await h.command(mod, bot.pending_command)
    assert [r.text for r in msg.replies] == ["🛡 Waiting for review: 0"]


# --- review requests & notifications ---------------------------------------------------


async def test_review_request_with_long_text_sends_photo_then_text(h):
    report_id = save(photo_file_id="PH", description="y" * 1500)
    await bot.send_review_request(h.context(make_user(MOD_ID)), report_id)
    calls = h.bot.to(MOD_ID)
    assert [c.method for c in calls] == ["send_photo", "send_message"]
    assert not hasattr(calls[0], "caption")


async def test_review_request_skips_missing_report_and_swallows_errors(h, caplog):
    ctx = h.context(make_user(MOD_ID))
    await bot.send_review_request(ctx, 404)
    assert h.bot.calls == []
    h.bot.fail_for(MOD_ID, TelegramError("blocked"))
    await bot.send_review_request(ctx, save())
    assert "Could not send report" in caplog.text


async def test_notify_moderators_swallows_errors(h, caplog):
    h.bot.fail_for(MOD_ID, TelegramError("blocked"))
    await bot.notify_moderators(h.context(make_user(1)), "hi")
    assert "Could not message moderator" in caplog.text


# --- publishing ------------------------------------------------------------------


async def test_publish_report_only_from_scheduled(h):
    report_id = save()  # pending_review
    assert await bot.publish_report(h.context(make_user(1)), report_id) is False
    assert h.bot.calls == []


async def test_publish_long_caption_sends_photo_then_text(h):
    report_id = save(status=bot.REPORT_SCHEDULED, photo_file_id="PH", description="z" * 1200)
    assert await bot.publish_report(h.context(make_user(1)), report_id)
    calls = h.bot.to(CHANNEL)
    assert [c.method for c in calls] == ["send_photo", "send_message"]
    assert calls[1].reply_markup is not None


async def test_publish_survives_unreachable_reporter(h):
    report_id = save(status=bot.REPORT_SCHEDULED)
    h.bot.fail_for(1, TelegramError("blocked"))
    assert await bot.publish_report(h.context(make_user(1)), report_id)
    assert status(report_id) == bot.REPORT_POSTED


# --- scheduler ----------------------------------------------------------------------


async def test_run_schedule_does_nothing_outside_window(h, clock):
    save(status=bot.REPORT_SCHEDULED)
    clock.set(2026, 11, 20, 8, 0)
    await bot.run_schedule(h.context(make_user(1)))
    assert h.bot.calls == []


async def test_run_schedule_publishes_only_due_reports(h, clock):
    clock.set(2026, 11, 20, 23, 0)
    due = save(status=bot.REPORT_SCHEDULED)  # publish_after = 21.11 09:00
    clock.set(2026, 11, 21, 9, 0)
    later = save(status=bot.REPORT_SCHEDULED)
    with bot.db_session() as db:
        db.execute(
            "UPDATE reports SET publish_after = ? WHERE id = ?",
            (bot.to_db_time(bot.now_local().replace(hour=15)), later),
        )
    await bot.run_schedule(h.context(make_user(1)))
    assert status(due) == bot.REPORT_POSTED
    assert status(later) == bot.REPORT_SCHEDULED


async def test_reminder_sent_once_close_to_deadline(h, clock):
    report_id = save()  # submitted 12:00 -> deadline 14:00
    ctx = h.context(make_user(1))

    clock.set(2026, 11, 20, 13, 20)
    await bot.run_schedule(ctx)
    assert h.bot.to(MOD_ID) == []

    clock.set(2026, 11, 20, 13, 31)
    await bot.run_schedule(ctx)
    await bot.run_schedule(ctx)
    reminders = h.bot.texts_to(MOD_ID)
    assert len(reminders) == 1
    assert f"Report #{report_id} is still waiting" in reminders[0]
    assert "20.11.2026 14:00" in reminders[0]


async def test_reminder_skips_reports_without_publish_time(h, clock):
    report_id = save()
    with bot.db_session() as db:
        db.execute("UPDATE reports SET publish_after = NULL WHERE id = ?", (report_id,))
    await bot.remind_moderators(h.context(make_user(1)))
    assert h.bot.calls == []


async def test_scheduler_without_moderators_skips_reminders(h, no_moderators, monkeypatch):
    called = []
    monkeypatch.setattr(bot, "remind_moderators", lambda ctx: called.append(ctx))
    await bot.run_schedule(h.context(make_user(1)))
    assert called == []


def test_review_text_for_overdue_report(clock):
    report_id = save()
    clock.set(2026, 11, 20, 15, 0)
    text = bot.make_review_text(bot.fetch_report(report_id))
    assert "Publish by: 20.11.2026 14:00" in text
    assert "published immediately" in text
    assert "From: @alice (ID 1)" in text


def test_planned_publish_time_without_stored_time(clock):
    row = {"publish_after": None}
    clock.set(2026, 11, 20, 3, 0)
    assert bot.planned_publish_time(row) == clock.now.replace(hour=9)
    assert bot.publish_deadline(row) is None


async def test_approve_when_buttons_already_removed(h, mod):
    report_id = save()
    q = await h.press(mod, f"mod:approve:{report_id}", edit_error=TelegramError("not modified"))
    assert status(report_id) == bot.REPORT_POSTED
    assert q.answers[0].text == "Approved."
