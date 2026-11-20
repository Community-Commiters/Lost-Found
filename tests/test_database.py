"""SQLite layer: schema, migrations of old databases, atomic status changes."""

import sqlite3

import pytest

import bot
from tests.conftest import make_flow, make_user, save


def columns(table):
    with bot.db_session() as db:
        return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def test_init_db_creates_tables_with_all_columns():
    assert set(bot.REPORT_MIGRATIONS) <= columns("reports")
    assert set(bot.CLAIM_MIGRATIONS) <= columns("claims")


def test_init_db_is_idempotent():
    bot.init_db()
    bot.init_db()
    assert "publish_after" in columns("reports")


def test_init_db_upgrades_a_legacy_database(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(bot, "DATABASE_FILE", str(path))
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE reports (id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
            "location TEXT NOT NULL, extra_count INTEGER NOT NULL, "
            "legacy_note TEXT NOT NULL, contact_info TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE claims (id INTEGER PRIMARY KEY, report_id INTEGER NOT NULL, "
            "claimant_id INTEGER NOT NULL, claimant_username TEXT, detail TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )

    bot.init_db()
    assert {"publish_after", "kind", "moderated_by"} <= columns("reports")
    assert {"claim_type", "status", "poster_message_id"} <= columns("claims")

    report_id = save(contact_type=bot.CONTACT_BOT, contact_value=None)
    row = bot.fetch_report(report_id)
    assert row["title"] == "Black wallet"  # legacy alias of description
    assert row["location"] == "Library"  # legacy alias of found_place
    assert row["extra_count"] == 0  # unknown required INTEGER
    assert row["legacy_note"] == ""  # unknown required TEXT
    assert row["contact_info"] == bot.CONTACT_BOT
    assert row["status"] == bot.REPORT_PENDING_REVIEW


def test_insert_row_none_into_required_column_becomes_empty_string():
    with bot.db_session() as db:
        db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT NOT NULL, b TEXT)")
        row_id = bot.insert_row(db, "t", {"a": None, "b": None, "ignored": 1})
        row = db.execute("SELECT * FROM t WHERE id = ?", (row_id,)).fetchone()
    assert row["a"] == "" and row["b"] is None


def test_insert_row_without_insertable_columns_raises():
    with bot.db_session() as db:
        db.execute("CREATE TABLE only_pk (id INTEGER PRIMARY KEY)")
        with pytest.raises(sqlite3.OperationalError, match="no insertable columns"):
            bot.insert_row(db, "only_pk", {})


def test_db_session_rolls_back_on_error():
    with bot.db_session() as db:
        db.execute("CREATE TABLE r (x INTEGER)")
    with pytest.raises(RuntimeError):
        with bot.db_session() as db:
            db.execute("INSERT INTO r VALUES (1)")
            raise RuntimeError("boom")
    with bot.db_session() as db:
        assert db.execute("SELECT COUNT(*) FROM r").fetchone()[0] == 0


def test_save_report_stores_pending_review_with_publish_time(clock):
    clock.set(2026, 11, 20, 23, 0)
    report_id = save(user=make_user(1, None), username=None)
    row = bot.fetch_report(report_id)
    assert row["status"] == bot.REPORT_PENDING_REVIEW
    assert row["username"] is None
    # 23:00 local -> next day 09:00 local == 04:00 UTC.
    assert row["publish_after"] == "2026-11-21T04:00:00+00:00"
    assert row["channel_message_id"] is None


def test_build_report_values_defaults_contact_to_bot():
    values = bot.build_report_values(make_flow(contact_type="", contact_value=None), make_user(1), "t")
    assert values["contact_type"] == bot.CONTACT_BOT
    assert values["handoff_location"] == "Room 101"
    assert values["title"] == values["description"]


def test_change_report_status_is_atomic():
    report_id = save()
    assert bot.change_report_status(report_id, bot.REPORT_PENDING_REVIEW, bot.REPORT_SCHEDULED, moderated_by=5)
    assert not bot.change_report_status(report_id, bot.REPORT_PENDING_REVIEW, bot.REPORT_REJECTED)
    row = bot.fetch_report(report_id)
    assert row["status"] == bot.REPORT_SCHEDULED and row["moderated_by"] == 5


def test_update_report_status_with_and_without_message_id():
    report_id = save()
    bot.update_report_status(report_id, bot.REPORT_FAILED)
    assert bot.fetch_report(report_id)["status"] == bot.REPORT_FAILED
    bot.update_report_status(report_id, bot.REPORT_POSTED, channel_message_id=77)
    row = bot.fetch_report(report_id)
    assert (row["status"], row["channel_message_id"]) == (bot.REPORT_POSTED, 77)


def test_update_report_status_swallows_database_errors(caplog):
    with bot.db_session() as db:
        db.execute("DROP TABLE reports")
    bot.update_report_status(1, bot.REPORT_POSTED)  # must not raise
    assert "Could not set status" in caplog.text


def test_fetch_report_ids_and_posted_report():
    a = save(status=bot.REPORT_POSTED)
    b = save()
    assert bot.fetch_report_ids("status = ? ORDER BY id", (bot.REPORT_POSTED,)) == [a]
    assert bot.fetch_posted_report(a)["id"] == a
    assert bot.fetch_posted_report(b) is None
    assert bot.fetch_report(12345) is None


def test_claims_round_trip():
    report_id = save(status=bot.REPORT_POSTED)
    claim_id = bot.save_claim(report_id, make_user(2, "bob"), "blue sticker", bot.CLAIM_GOT_ITEM)
    bot.set_claim_poster_message(claim_id, 55)
    bot.set_claim_status(claim_id, bot.CLAIM_APPROVED)
    claim, report = bot.fetch_claim_with_report(claim_id)
    assert claim["claimant_username"] == "@bob"
    assert claim["poster_message_id"] == 55
    assert claim["status"] == bot.CLAIM_APPROVED
    assert report["id"] == report_id


def test_fetch_claim_with_report_missing_rows():
    assert bot.fetch_claim_with_report(404) == (None, None)
    claim_id = bot.save_claim(31337, make_user(2), "x", bot.CLAIM_GOT_ITEM)
    claim, report = bot.fetch_claim_with_report(claim_id)
    assert claim is not None and report is None
