
Lost&Found bot - https://t.me/NewUULostFoundBot

FOR MacOS

Copy bot.py and requirements.txt into your existing lost-found-bot folder. Keep your existing .env file. Required entries:

BOT_TOKEN=...
CHANNEL_ID=@your_public_channel (or private numeric chat ID)
ADMIN_USER_ID=your_telegram_user_id
Activate your virtual environment and install dependencies:

cd lost-found-bot
source .venv/bin/activate
python -m pip install -r requirements.txt
Stop the currently running bot with CMD+C, then run:

python bot.py
In Telegram, send /start, tap FOUND, test Skip photo, and complete a test report.

The bot creates lost_found.db automatically in the same folder.
