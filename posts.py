import html
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
import database
import keyboards
from states import PostForm
from utils import compute_publish_after

router = Router()


async def _start_post(callback: CallbackQuery, state: FSMContext, post_type: str) -> None:
    await state.clear()
    await state.update_data(post_type=post_type)
    await state.set_state(PostForm.title)
    await callback.message.answer(f"📝 <b>New #{post_type} post</b>\n\nSend a short title (e.g. “Black wallet”).")
    await callback.answer()


@router.callback_query(F.data == "menu:found")
async def menu_found(callback: CallbackQuery, state: FSMContext) -> None:
    await _start_post(callback, state, "FOUND")


@router.callback_query(F.data == "menu:lost")
async def menu_lost(callback: CallbackQuery, state: FSMContext) -> None:
    await _start_post(callback, state, "LOST")


@router.message(PostForm.title, F.text)
async def got_title(message: Message, state: FSMContext) -> None:
    if len(message.text) > 100:
        await message.answer("Title is too long (max 100 characters). Please shorten it.")
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(PostForm.description)
    await message.answer("Now send a description (color, brand, distinguishing marks).")


@router.message(PostForm.description, F.text)
async def got_description(message: Message, state: FSMContext) -> None:
    if len(message.text) > 800:
        await message.answer("Description is too long (max 800 characters).")
        return
    await state.update_data(description=message.text.strip())
    await state.set_state(PostForm.location)
    await message.answer("Where was it?", reply_markup=keyboards.locations_keyboard())


@router.callback_query(PostForm.location, F.data.startswith("loc:"))
async def got_location(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.split(":")[1])
    await state.update_data(location=config.LOCATIONS[index])
    await state.set_state(PostForm.date)
    await callback.message.answer("On what date? Send it as DD.MM.YYYY (e.g. 27.10.2026).")
    await callback.answer()


@router.message(PostForm.date, F.text)
async def got_date(message: Message, state: FSMContext) -> None:
    try:
        parsed = datetime.strptime(message.text.strip(), "%d.%m.%Y")
    except ValueError:
        await message.answer("Invalid date. Please use the format DD.MM.YYYY.")
        return
    if parsed > datetime.now():
        await message.answer("The date can't be in the future.")
        return
    await state.update_data(item_date=parsed.strftime("%d.%m.%Y"))
    await state.set_state(PostForm.photo)
    await message.answer("Send a photo of the item (optional).", reply_markup=keyboards.skip_photo_keyboard())


async def _after_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data["post_type"] == "FOUND":
        await state.set_state(PostForm.destination)
        await message.answer("Where is the item now?", reply_markup=keyboards.destination_keyboard())
    else:
        await state.update_data(at_student_affairs=False)
        await _show_summary(message, state)


@router.message(PostForm.photo, F.photo)
async def got_photo(message: Message, state: FSMContext) -> None:
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await _after_photo(message, state)


@router.callback_query(PostForm.photo, F.data == "photo:skip")
async def skip_photo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(photo_file_id=None)
    await callback.answer()
    await _after_photo(callback.message, state)


@router.callback_query(PostForm.destination, F.data.startswith("dest:"))
async def got_destination(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(at_student_affairs=callback.data == "dest:sa")
    await callback.answer()
    await _show_summary(callback.message, state)


async def _show_summary(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(PostForm.confirm)
    where = ""
    if data["post_type"] == "FOUND":
        where = "\n🏢 At Student Affairs" if data["at_student_affairs"] else "\n🤝 Finder will return it directly"
    text = (
        f"<b>#{data['post_type']}: {html.escape(data['title'])}</b>\n"
        f"{html.escape(data['description'])}\n\n"
        f"📍 {html.escape(data['location'])}\n"
        f"📅 {data['item_date']}{where}\n\n"
        "Submit this post?"
    )
    await message.answer(text, reply_markup=keyboards.confirm_keyboard())


@router.callback_query(PostForm.confirm, F.data == "confirm:yes")
async def confirm_yes(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user = callback.from_user
    await database.upsert_user(user.id, user.username, user.full_name)
    post_id = await database.create_post(
        post_type=data["post_type"],
        author_id=user.id,
        title=data["title"],
        description=data["description"],
        location=data["location"],
        item_date=data["item_date"],
        photo_file_id=data.get("photo_file_id"),
        at_student_affairs=data.get("at_student_affairs", False),
        publish_after=compute_publish_after(),
    )
    await state.clear()
    await callback.message.answer(
        f"✅ Post #{post_id} submitted. It will appear in the channel within 2 hours "
        "(between 09:00 and 22:00; otherwise the next morning)."
    )
    await callback.answer()


@router.callback_query(PostForm.confirm, F.data == "confirm:no")
async def confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Cancelled. Send /start to return to the menu.")
    await callback.answer()