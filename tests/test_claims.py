"""Responses to published posts: claims, poster approval, contact sharing."""

from telegram.error import Forbidden, TelegramError

import bot
from tests.conftest import MOD_ID, make_user, save

POSTED = bot.REPORT_POSTED


# --- "I think this is mine" (FOUND posts) ------------------------------------


async def test_claim_button_on_unavailable_report(h, bob):
    report_id = save()  # not posted
    q = await h.press(bob, f"claim:{report_id}")
    assert q.answers[-1].text == "This announcement is no longer available."


async def test_claim_button_on_own_report(h, alice):
    report_id = save(status=POSTED)
    q = await h.press(alice, f"claim:{report_id}")
    assert q.answers[-1].text == "This is your own report."


async def test_claim_button_opens_private_chat(h, bob):
    report_id = save(status=POSTED)
    q = await h.press(bob, f"claim:{report_id}", chat_id=-100)
    texts = h.bot.texts_to(bob.id)
    assert f"found-item report #{report_id}" in texts[0] and "Black wallet" in texts[0]
    keyboard = h.bot.to(bob.id)[1].reply_markup.inline_keyboard
    assert keyboard[0][0].callback_data == f"claim_choice:{report_id}:got_item"
    assert q.answers[-1].text == "Check your private chat with the bot."


async def test_claim_button_when_bot_cannot_message_user(h, bob):
    report_id = save(status=POSTED)
    h.bot.fail_for(bob.id, Forbidden("bot can't initiate conversation"))
    q = await h.press(bob, f"claim:{report_id}")
    assert "press Start" in q.answers[-1].text


async def test_claim_button_other_telegram_error(h, bob):
    report_id = save(status=POSTED)
    h.bot.fail_for(bob.id, TelegramError("timeout"))
    q = await h.press(bob, f"claim:{report_id}")
    assert "couldn't message you privately" in q.answers[-1].text


# --- "I found this item" (LOST posts) -----------------------------------------


async def test_lost_response_rejects_found_report(h, bob):
    report_id = save(status=POSTED)  # FOUND
    q = await h.press(bob, f"lost_response:{report_id}")
    assert "lost-item announcement is no longer available" in q.answers[-1].text


async def test_lost_response_on_own_report(h, alice):
    report_id = save(status=POSTED, kind=bot.KIND_LOST)
    q = await h.press(alice, f"lost_response:{report_id}")
    assert q.answers[-1].text == "This is your own report."


async def test_lost_response_errors(h, bob):
    report_id = save(status=POSTED, kind=bot.KIND_LOST)
    h.bot.fail_for(bob.id, Forbidden("blocked"))
    q = await h.press(bob, f"lost_response:{report_id}")
    assert "press Start" in q.answers[-1].text
    h.bot.fail_for(bob.id, TelegramError("timeout"))
    q = await h.press(bob, f"lost_response:{report_id}")
    assert "couldn't message you privately" in q.answers[-1].text
    assert bot.CLAIM_KEY not in h.user_data[bob.id]


async def test_lost_response_full_flow(h, alice, bob):
    report_id = save(status=POSTED, kind=bot.KIND_LOST, contact_type=bot.CONTACT_BOT, contact_value=None)
    await h.press(bob, f"lost_response:{report_id}")
    assert h.user_data[bob.id][bot.CLAIM_KEY] == (report_id, bot.CLAIM_FOUND_ITEM)

    msg = await h.text(bob, "Found it at the bus stop, it has a red tag")
    assert "Thank you" in msg.last
    to_alice = h.bot.to(alice.id)[-1]
    assert "This person says they found your lost item" in to_alice.text
    assert "Responder: @bob" in to_alice.text

    claim_id = 1
    await h.press(alice, f"claim_decision:{claim_id}:approve")
    assert "Telegram: @alice" in h.bot.texts_to(bob.id)[-1]


# --- claim choice & details -------------------------------------------------------------


async def test_claim_choice_got_item_then_details(h, alice, bob):
    report_id = save(status=POSTED)
    q = await h.press(bob, f"claim_choice:{report_id}:got_item")
    assert q.edited == [None]
    assert "decide whether to share their contact" in q.message.last
    assert h.user_data[bob.id][bot.CLAIM_KEY] == (report_id, bot.CLAIM_GOT_ITEM)

    # Details win over menu-button parsing while a claim is pending.
    msg = await h.text(bob, "Has a scratch on the back")
    assert "sent privately" in msg.last
    assert bot.CLAIM_KEY not in h.user_data[bob.id]

    claim, _ = bot.fetch_claim_with_report(1)
    assert claim["detail"] == "Has a scratch on the back"
    assert claim["claim_type"] == bot.CLAIM_GOT_ITEM
    assert claim["poster_message_id"] is not None

    to_alice = h.bot.to(alice.id)[-1]
    assert "Their response: I got my lost item" in to_alice.text
    assert to_alice.reply_markup.inline_keyboard[0][0].callback_data == "claim_decision:1:approve"
    assert "New claim #1" in h.bot.texts_to(MOD_ID)[-1]


async def test_claim_choice_contact_poster_tolerates_edit_error(h, bob):
    report_id = save(status=POSTED)
    q = await h.press(bob, f"claim_choice:{report_id}:contact_poster", edit_error=TelegramError("x"))
    assert "must approve before their contact" in q.message.last


async def test_claim_choice_unavailable_or_own(h, alice, bob):
    q = await h.press(bob, "claim_choice:404:got_item")
    assert "no longer available" in q.answers[-1].text
    report_id = save(status=POSTED)
    q = await h.press(alice, f"claim_choice:{report_id}:got_item")
    assert q.answers[-1].text == "This is your own report."


async def test_claim_detail_must_be_text(h, bob):
    report_id = save(status=POSTED)
    await h.press(bob, f"claim_choice:{report_id}:got_item")
    msg = await h.text(bob, "   ")
    assert msg.last == "Please send the item details as text."
    assert bot.CLAIM_KEY in h.user_data[bob.id]


async def test_claim_detail_after_report_removed(h, bob):
    report_id = save(status=POSTED)
    await h.press(bob, f"claim_choice:{report_id}:got_item")
    bot.update_report_status(report_id, bot.REPORT_REJECTED)
    msg = await h.text(bob, "details")
    assert "no longer available" in msg.last
    assert bot.CLAIM_KEY not in h.user_data[bob.id]


async def test_receive_claim_detail_without_pending_claim(h, bob):
    msg = await h.text(bob, "x")  # goes to menu, not to claim handler
    from types import SimpleNamespace

    await bot.receive_claim_detail(
        SimpleNamespace(effective_message=msg, effective_user=bob), h.context(bob)
    )
    assert len(msg.replies) == 1


async def test_poster_unreachable_marks_claim_and_tells_claimant(h, alice, bob):
    report_id = save(status=POSTED)
    await h.press(bob, f"claim_choice:{report_id}:got_item")
    h.bot.fail_for(alice.id, Forbidden("blocked"))
    await h.text(bob, "details")
    claim, _ = bot.fetch_claim_with_report(1)
    assert claim["status"] == bot.CLAIM_NOTIFICATION_FAILED
    assert "Telegram would not let the bot notify the poster" in h.bot.texts_to(bob.id)[-1]


async def test_poster_notification_generic_error(h, alice, bob):
    report_id = save(status=POSTED)
    await h.press(bob, f"claim_choice:{report_id}:got_item")
    h.bot.fail_for(alice.id, TelegramError("timeout"))
    await h.text(bob, "details")
    assert "I could not notify the poster" in h.bot.texts_to(bob.id)[-1]


# --- poster decision --------------------------------------------------------------------


def make_claim(report_status=POSTED, **report):
    report_id = save(status=report_status, **report)
    return bot.save_claim(report_id, make_user(2, "bob"), "scratch", bot.CLAIM_GOT_ITEM)


async def test_decision_approve_shares_contact(h, alice, bob):
    claim_id = make_claim(contact_type=bot.CONTACT_PHONE, contact_value="+998 90 1")
    q = await h.press(alice, f"claim_decision:{claim_id}:approve")
    assert q.answers[-1].text == "Contact sharing approved."
    assert q.edited == [None]
    note = h.bot.texts_to(bob.id)[-1]
    assert "Good news!" in note and "Phone: +998 90 1" in note
    assert bot.fetch_claim_with_report(claim_id)[0]["status"] == bot.CLAIM_APPROVED


async def test_decision_decline(h, alice, bob):
    claim_id = make_claim()
    q = await h.press(alice, f"claim_decision:{claim_id}:decline", edit_error=TelegramError("x"))
    assert q.answers[-1].text == "Claim declined."
    assert "declined to share" in h.bot.texts_to(bob.id)[-1]
    assert bot.fetch_claim_with_report(claim_id)[0]["status"] == bot.CLAIM_DECLINED


async def test_decision_twice_is_refused(h, alice, bob):
    claim_id = make_claim()
    await h.press(alice, f"claim_decision:{claim_id}:approve")
    q = await h.press(alice, f"claim_decision:{claim_id}:decline")
    assert q.answers[-1].text == "This claim has already been handled."
    assert len(h.bot.to(bob.id)) == 1


async def test_decision_only_by_poster(h, bob):
    claim_id = make_claim()
    q = await h.press(bob, f"claim_decision:{claim_id}:approve")
    assert "Only the person who posted" in q.answers[-1].text


async def test_decision_missing_claim_or_report(h, alice):
    q = await h.press(alice, "claim_decision:404:approve")
    assert q.answers[-1].text == "This claim no longer exists."
    claim_id = bot.save_claim(31337, make_user(2), "x", bot.CLAIM_GOT_ITEM)
    q = await h.press(alice, f"claim_decision:{claim_id}:approve")
    assert q.answers[-1].text == "The original report no longer exists."


# --- misc -----------------------------------------------------------------------------


async def test_unknown_button(h, alice):
    q = await h.press(alice, "old:button:from:v1")
    assert q.answers[-1].text == "Unknown button."


async def test_error_handler_logs(caplog):
    from types import SimpleNamespace

    await bot.error_handler(None, SimpleNamespace(error=ValueError("boom")))
    assert "An error occurred" in caplog.text
