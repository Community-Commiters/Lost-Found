from aiogram.fsm.state import State, StatesGroup


class PostForm(StatesGroup):
    title = State()
    description = State()
    location = State()
    date = State()
    photo = State()
    destination = State()
    confirm = State()