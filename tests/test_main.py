"""Application wiring in main(), without starting real polling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

import bot


class FakeApp:
    def __init__(self, with_job_queue=True):
        self.handlers, self.error_handlers = [], []
        self.polling = False
        self.job_queue = SimpleNamespace(run_repeating=MagicMock()) if with_job_queue else None

    def add_handler(self, handler):
        self.handlers.append(handler)

    def add_error_handler(self, handler):
        self.error_handlers.append(handler)

    def run_polling(self):
        self.polling = True


def install(monkeypatch, app):
    tokens = []

    class Builder:
        def token(self, value):
            tokens.append(value)
            return self

        def build(self):
            return app

    monkeypatch.setattr(bot, "Application", SimpleNamespace(builder=Builder))
    return tokens


def test_main_registers_every_handler_and_the_scheduler(monkeypatch):
    app = FakeApp()
    tokens = install(monkeypatch, app)
    bot.main()

    assert tokens == [bot.BOT_TOKEN]
    commands = sorted(c for h in app.handlers if isinstance(h, CommandHandler) for c in h.commands)
    assert commands == ["cancel", "found", "lost", "pending", "start"]
    callbacks = [h for h in app.handlers if isinstance(h, CallbackQueryHandler)]
    assert len(callbacks) == len(bot.CALLBACK_ROUTES) + 1  # + unknown-button fallback
    assert callbacks[-1].callback is bot.on_unknown_button
    assert sum(isinstance(h, MessageHandler) for h in app.handlers) == 2
    assert app.error_handlers == [bot.error_handler]
    app.job_queue.run_repeating.assert_called_once()
    assert app.job_queue.run_repeating.call_args.args[0] is bot.run_schedule
    assert app.polling


def test_main_warns_without_moderators(monkeypatch, no_moderators, caplog):
    install(monkeypatch, FakeApp())
    bot.main()
    assert "approved automatically" in caplog.text


def test_main_requires_job_queue(monkeypatch):
    install(monkeypatch, FakeApp(with_job_queue=False))
    with pytest.raises(RuntimeError, match="scheduler is not installed"):
        bot.main()
