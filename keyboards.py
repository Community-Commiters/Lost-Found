import config


def locations_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(config.LOCATIONS):
        kb.button(text=name, callback_data=f"loc:{i}")
    kb.adjust(2)
    return kb.as_markup()


def skip_photo_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Skip photo", callback_data="photo:skip")
    return kb.as_markup()


def destination_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏢 I handed it to Student Affairs", callback_data="dest:sa")
    kb.button(text="🤝 I will return it to the owner myself", callback_data="dest:direct")
    kb.adjust(1)
    return kb.as_markup()


def confirm_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Submit", callback_data="confirm:yes")
    kb.button(text="❌ Cancel", callback_data="confirm:no")
    kb.adjust(2)
    return kb.as_markup()