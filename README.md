University Lost & Found Telegram Bot — Project Documentation

1. Project Overview
The University Lost & Found Telegram Bot helps university students report lost and found items through Telegram.
Instead of searching manually or asking other students, users can submit reports directly through the bot.
The bot provides the following features:
FOUND reports: Students can report items they have found.
LOST reports: Students can report items they have lost.
Step-by-step conversations: The bot asks questions and collects information.
Photo attachments: Users can include pictures of items.
Moderator approval: Every report is reviewed before publication.
Scheduled publishing: Approved reports are published between 09:00 and 22:00.
Private communication: People can respond to announcements privately through the bot.
Contact protection: The original poster must approve before their contact information is shared.
The project is developed using Python, the Telegram Bot API, and SQLite.
2. Division of Work Between Developers and stakeholders
Project Manager - 
Developer 1 -
Developer 2 -
Moderator and support -
Administrator and policy writer -
4. Configuration and Environment Variables

The first part of the code begins with importing the required Python libraries.

Some important libraries are:

os: Reads environment variables and configuration.

logging: Records errors and application activity.

sqlite3: Connects to the SQLite database.

datetime: Handles dates and publishing times.

dataclasses: Creates structured objects for storing information.

Enum: Defines the different conversation steps.

telegram: Provides Telegram bot functionality.

dotenv: Loads configuration values from the .env file.

The function require_env() checks whether an important environment variable exists.

For example:

python
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing from your .env file.")
    return value
This function prevents the bot from starting if an essential configuration value is missing.

The bot requires two main configuration values:

BOT_TOKEN: The Telegram bot token.

CHANNEL_ID: The Telegram channel where announcements will be published.

The moderator IDs are loaded using parse_user_ids(). This function allows multiple moderators to be configured.

The TIMEZONE variable defines the time zone used for scheduling. By default, it is set to Asia/Tashkent.

4. Constants and Conversation Steps

The project uses constants instead of writing the same text repeatedly.

For example:

python
KIND_FOUND = "FOUND"
KIND_LOST = "LOST"
These constants identify the two types of reports.

The bot also uses report statuses:

pending_review: The report is waiting for moderator approval.

scheduled: The report has been approved and is waiting to be published.

publishing: The bot is currently publishing the announcement.

posted: The announcement has been successfully published.

failed: Publishing was unsuccessful.

rejected: A moderator rejected the report.

The Step enumeration defines the questions that users answer during report creation.

python
class Step(Enum):
    PHOTO = "photo"
    DESCRIPTION = "description"
    LOCATION = "location"
    LOCATION_DETAIL = "location_detail"
    HANDOFF = "handoff"
    HANDOFF_DETAIL = "handoff_detail"
    CONTACT = "contact"
    CONTACT_FALLBACK = "contact_fallback"
    PHONE = "phone"
    PREVIEW = "preview"
    SUBMITTING = "submitting"
For example, when a user is answering the description question, the bot knows that the current step is Step.DESCRIPTION.

This is important because the bot must understand which answer it is currently receiving.

5. ReportFlow: Collecting User Information

One of the most important structures in the project is the ReportFlow dataclass.

It stores all information collected from a user during a report session.

python
@dataclass
class ReportFlow:
    kind: str
    step: Step
    user_id: int
    username: Optional[str]
    photo_file_id: Optional[str] = None
    description: str = ""
    found_place: str = ""
    found_detail: str = ""
    handoff: str = ""
    handoff_detail: str = ""
    contact_type: str = ""
    contact_value: Optional[str] = None
Each field has a specific purpose:

Field

Purpose

kind

FOUND or LOST

step

Current conversation step

user_id

Telegram ID of the reporter

username

Reporter's Telegram username

photo_file_id

Telegram photo identifier

description

Description of the item

found_place

Selected university location

found_detail

Exact location details

handoff

Where the found item was left or handed in

handoff_detail

Additional handover information

contact_type

Preferred contact method

contact_value

Phone number or username

The is_lost property checks whether the current report is a LOST report.

This makes it easier to use the same functions for both report types.

Managing conversation sessions

The function get_flow() retrieves the current report session from Telegram's context.user_data.

The function clear_session() removes the active report, pending claim, and rejection information.

Telegram's context.user_data is useful because it allows the bot to remember information about an individual user betweenmessages.

For example, if a student starts a FOUND report and sends a photo, the bot remembers that photo when asking the nextquestion.

6. Step-by-Step Report Creation

The bot uses different conversation paths for FOUND and LOST reports.

FOUND report process

Upload a photo or skip.
Describe the found item.
Select where it was found and specify the exact location.
Explain where the item was left or handed in.
Choose a contact method.
Review the announcement and submit it.
LOST report process

Describe the lost item.
Select the most likely location and specify details.
Upload a photo or skip.
Choose a contact method.
Review the announcement and submit it.
The functions responsible for displaying questions include:

prompt_photo()

prompt_description()

prompt_location()

prompt_handoff()

prompt_contact()

show_preview()

Each function sends a message to the user and updates the current conversation step.

For example:

python
async def prompt_description(chat_id, context, flow):
    label = step_label(flow, Step.DESCRIPTION)

    if flow.is_lost:
        text = f"{label}\n\nPlease describe the item you lost."
    else:
        text = f"{label}\n\nWhat did you find left? Give short description"

    await send_prompt(
        context,
        chat_id,
        flow,
        Step.DESCRIPTION,
        text
    )
The function checks the report type and displays the appropriate question.

The async keyword is used because Telegram operations are asynchronous. This allows the bot to communicate withTelegram without blocking other operations.

7. Telegram Keyboards and Buttons

The bot uses two types of keyboards.

Reply keyboards appear at the bottom of the Telegram chat and allow users to select FOUND or LOST.

Inline keyboards appear directly below messages and allow users to perform specific actions.

Some examples are:

main_keyboard(): Displays FOUND and LOST buttons.

photo_keyboard(): Allows users to skip a photo.

location_keyboard(): Displays university locations.

handoff_keyboard(): Displays handover options.

contact_keyboard(): Displays contact methods.

preview_keyboard(): Allows users to submit their report.

The helper function button() creates an inline button:

python
def button(text: str, callback_data: str):
    return InlineKeyboardButton(
        text,
        callback_data=callback_data
    )
The callback_data value identifies which action should happen when a user presses the button.

For example, selecting a location sends information such as:

python
location:2
The bot uses this information to identify the selected location.

8. Processing User Messages

The bot has separate handlers for photos, text messages, and button presses.

Photo handling

The handle_photo() function processes photos sent by users.

It checks whether the user is currently at the photo step.

If the photo is valid for the current step, the bot saves its Telegram file_id in the ReportFlow object.

python
flow.photo_file_id = update.effective_message.photo[-1].file_id
Telegram stores the actual photo, so the bot only needs its file identifier to reuse it later.

Text handling

The handle_text() function processes ordinary text messages.

It first checks whether the user is:

Sending a moderator rejection reason.

Sending claim verification details.

Selecting FOUND or LOST.

Answering a question in an active report.

If the user is answering a report question, the bot finds the correct handler through TEXT_STEP_HANDLERS.

For example:

python
TEXT_STEP_HANDLERS = {
    Step.DESCRIPTION: on_text_at_description,
    Step.LOCATION_DETAIL: on_text_at_location_detail,
    Step.HANDOFF_DETAIL: on_text_at_handoff_detail,
    Step.PHONE: on_text_at_phone,
}
This dictionary connects each conversation step to its corresponding function.

It makes the code easier to understand because every step has its own handler.

Button callbacks

Functions such as on_skip_photo(), on_location_chosen(), on_handoff_chosen(), and on_contact_chosen() handle button presses.

For example, when a user selects a phone number as their contact method, the bot changes the current step to Step.PHONEand asks them to enter their number.

If the user chooses Telegram but does not have a username, the bot provides alternative contact methods.

9. Formatting Announcements

After collecting all the required information, the bot creates an announcement.

The main function responsible for this is:

python
make_announcement()
It uses helper functions:

format_place(): Formats the item location.

format_handoff(): Formats handover information.

format_contact(): Formats the contact instructions.

For a FOUND report, the announcement contains:

Item name or description.

Photo information.

Location where the item was found.

Where the item was left or handed in.

Contact instructions.

Report date.

For a LOST report, it contains:

Item name or description.

Photo information.

Probable lost location.

Contact instructions.

Report date.

The function also checks the report type to produce the correct announcement format.

This ensures that channel posts are readable and consistent.

10. SQLite Database Management

The project uses SQLite to permanently store reports and claims.

The database file is:

python
DATABASE_FILE = "lost_found.db"
There are two main tables.

Reports table

The reports table stores information about lost and found items.

Important columns include:

id: Unique report ID.

user_id: ID of the person who submitted the report.

description: Item description.

photo_file_id: Photo identifier.

found_place: Location.

contact_type: Contact method.

contact_value: Contact information.

created_at: Submission date.

status: Current report status.

channel_message_id: ID of the published Telegram message.

kind: FOUND or LOST.

Claims table

The claims table stores responses from people who interact with announcements.

Important columns include:

id: Unique claim ID.

report_id: Related report.

claimant_id: Person responding.

detail: Verification information.

created_at: Claim submission date.

claim_type: Type of response.

status: Current claim status.

The FOREIGN KEY connects each claim to its original report.

Database functions

The following functions manage database operations:

Function

Purpose

db_session()

Opens and closes database connections

init_db()

Creates or upgrades database tables

insert_row()

Inserts information into a table

save_report()

Saves a new report

save_claim()

Saves a new claim

fetch_report()

Retrieves a report

fetch_report_ids()

Retrieves report IDs

change_report_status()

Changes report status safely

update_report_status()

Updates a report's status

set_claim_status()

Updates a claim's status

The db_session() function uses a context manager.

This means the database connection is automatically closed after the operation, even if an error occurs.

The code also includes migration functions.

These functions allow older database versions to receive new columns without deleting existing records.

This is useful when developers add new features to the bot after its initial release.

11. Saving a New Report

When a user presses the Submit button, the on_publish() function is called.

Despite its name, this function does not immediately publish the announcement.

Instead, it:

Checks whether the report is ready.

Changes the step to SUBMITTING to prevent duplicate submissions.

Saves the report in SQLite.

Removes the active conversation session.

Sends confirmation to the user.

Sends the report to moderators for review.

If no moderators are configured, the bot automatically approves the report.

This separation is important because reports must be reviewed before appearing in the public channel.
