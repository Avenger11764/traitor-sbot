
import logging
import asyncio
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import random
from datetime import datetime, time, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
import os

# --- Configuration ---
# IMPORTANT: Replace "YOUR_TELEGRAM_BOT_TOKEN" with the token you get from @BotFather
TELEGRAM_BOT_TOKEN = "7314759198:AAGsFx3s8VJ97o6Et_M5KaIQZR1vbLTD74k"
GAME_STATE_FILE = "traitors_gamestate.json"

# --- Bot Setup ---
# Enable logging to see errors and bot activity
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

# --- Game Constants ---
TEAM_STARTING_POINTS = 1000
BLACKMAIL_STEAL_AMOUNT = 40
# NUM_TRAITORS is now dynamic, this is a fallback/max.
DEFAULT_NUM_TRAITORS = 3

# --- Game State Management ---
# This dictionary will hold the state of all games, keyed by chat_id.
games = {}

# --- Helper Functions ---

def save_game_state():
    """Saves the current game state to a JSON file."""
    try:
        with open(GAME_STATE_FILE, 'w') as f:
            json.dump(games, f, indent=4)
    except Exception as e:
        logger.error(f"Could not save game state: {e}")

def load_game_state():
    """Loads the game state from a JSON file if it exists."""
    global games
    if os.path.exists(GAME_STATE_FILE):
        try:
            with open(GAME_STATE_FILE, 'r') as f:
                games = json.load(f)
                # Convert string keys back to int for player dicts
                for chat_id, game_state in games.items():
                    if 'players' in game_state:
                        game_state['players'] = {int(k): v for k, v in game_state['players'].items()}
                logger.info("Game state loaded successfully.")
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Could not load game state: {e}")
            games = {}
    else:
        games = {}

def escape_markdown(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))

def get_game_state(chat_id):
    """Safely retrieves the game state for a given chat."""
    return games.get(str(chat_id))

def get_player_by_id(game_state, user_id):
    """Finds a player in the game state by their user_id."""
    return game_state["players"].get(user_id)

async def send_private_message(context: ContextTypes.DEFAULT_TYPE, user_id, text, reply_markup=None):
    """Sends a protected private message to a user, handling potential errors."""
    try:
        sent_message = await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2,
            protect_content=True  # This prevents forwarding and screenshots
        )
        return sent_message
    except Exception as e:
        logger.error(f"Could not send message to user {user_id}: {e}")
        return None

async def announce_elimination_winner(context: ContextTypes.DEFAULT_TYPE, chat_id, winner_team):
    """Announces the winner based on elimination and cleans up the game."""
    game_state = get_game_state(chat_id)
    if not game_state:
        return

    final_traitors = [escape_markdown(p['name']) for p in game_state["players"].values() if p['role'] == 'Traitor']

    result_text = (
        rf"🏆 *The Game Has Ended\!* 🏆\n\n"
        rf"All members of the *{escape_markdown(winner_team)}* team's opposition have been banished\.\n\n"
        rf"The final Traitors were: {', '.join(final_traitors)}\n\n"
        rf"🎉 The *{escape_markdown(winner_team)}* team wins by elimination\!"
    )
    await context.bot.send_message(chat_id, result_text, parse_mode=ParseMode.MARKDOWN_V2)

    if str(chat_id) in games:
        del games[str(chat_id)]
    save_game_state()

async def announce_points_winner(context: ContextTypes.DEFAULT_TYPE, chat_id, winner_team):
    """Announces the winner based on points and cleans up the game."""
    game_state = get_game_state(chat_id)
    if not game_state:
        return

    final_traitors = [escape_markdown(p['name']) for p in game_state["players"].values() if p['role'] == 'Traitor']
    losing_team = "Traitors" if winner_team == "Faithful" else "Faithful"

    result_text = (
        rf"🏆 *The Game Has Ended\!* 🏆\n\n"
        rf"The *{escape_markdown(losing_team)}* team has run out of points\.\n\n"
        rf"The final Traitors were: {', '.join(final_traitors)}\n\n"
        rf"🎉 The *{escape_markdown(winner_team)}* team wins by bankrupting the opposition\!"
    )
    await context.bot.send_message(chat_id, result_text, parse_mode=ParseMode.MARKDOWN_V2)

    if str(chat_id) in games:
        del games[str(chat_id)]
    save_game_state()

async def check_for_elimination_win(context: ContextTypes.DEFAULT_TYPE, chat_id):
    """Checks if a team has won by eliminating the other. Returns True if game ended."""
    game_state = get_game_state(chat_id)
    if not game_state:
        return False

    active_traitors = [p for p in game_state["players"].values() if p["role"] == "Traitor" and p["status"] == "Active"]
    active_faithful = [p for p in game_state["players"].values() if p["role"] == "Faithful" and p["status"] == "Active"]

    winner = None
    if not active_traitors:
        winner = "Faithful"
    elif not active_faithful:
        winner = "Traitors"

    if winner:
        await announce_elimination_winner(context, chat_id, winner)
        return True
    
    return False

async def check_for_points_win(context: ContextTypes.DEFAULT_TYPE, chat_id):
    """Checks if a team has won by having zero points. Returns True if game ended."""
    game_state = get_game_state(chat_id)
    if not game_state:
        return False

    faithful_team_points = sum(p['points'] for p in game_state['players'].values() if p['role'] == 'Faithful')
    traitor_team_points = sum(p['points'] for p in game_state['players'].values() if p['role'] == 'Traitor')

    winner = None
    if traitor_team_points <= 0:
        winner = "Faithful"
    elif faithful_team_points <= 0:
        winner = "Traitors"

    if winner:
        await announce_points_winner(context, chat_id, winner)
        return True

    return False

# --- Game Phase Functions ---

async def begin_day_phase(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Starts the day's announcements and initiates the vote. Used for manual starts."""
    game_state = get_game_state(chat_id)
    if not game_state or game_state["phase"] != "NIGHT":
        return

    # Invalidate any pending night actions
    if game_state.get('active_night_prompt'):
        prompt_info = game_state['active_night_prompt']
        try:
            await context.bot.edit_message_text(
                chat_id=prompt_info['user_id'],
                message_id=prompt_info['message_id'],
                text=r"Time's up\! The day has begun, and your chance to act has passed\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Could not edit expired night prompt: {e}")
        game_state['active_night_prompt'] = None

    game_state["phase"] = "DAY"
    game_state["day"] += 1
    game_state["votes"] = {}
    game_state["poll_message_id"] = None
    save_game_state()

    day_num_escaped = escape_markdown(game_state['day'])
    await context.bot.send_message(chat_id, rf"🌅 *Day {day_num_escaped} Begins*\n\nLet's see what happened overnight\.", parse_mode=ParseMode.MARKDOWN_V2)
    await asyncio.sleep(1)

    if game_state.get("murdered_last_night"):
        murdered_player = get_player_by_id(game_state, game_state["murdered_last_night"])
        if murdered_player:
            await context.bot.send_message(chat_id, rf"A grim discovery was made\. Last night, *{escape_markdown(murdered_player['name'])}* was murdered by the Traitors\!", parse_mode=ParseMode.MARKDOWN_V2)
        game_state["murdered_last_night"] = None
        save_game_state()
    else:
        await context.bot.send_message(chat_id, r"The night was quiet\. No one was murdered\.")

    await asyncio.sleep(1)
    await display_team_scores(context, chat_id)
    await asyncio.sleep(1)

    await initiate_vote_poll(context, chat_id)

async def transition_to_day_discussion(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Transitions from a timed Night to a timed Day for discussion."""
    game_state = get_game_state(chat_id)
    if not game_state or game_state["phase"] != "NIGHT":
        return

    # Invalidate any pending night actions
    if game_state.get('active_night_prompt'):
        prompt_info = game_state['active_night_prompt']
        try:
            await context.bot.edit_message_text(
                chat_id=prompt_info['user_id'],
                message_id=prompt_info['message_id'],
                text=r"Time's up\! The day has begun, and your chance to act has passed\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Could not edit expired night prompt: {e}")
        game_state['active_night_prompt'] = None

    game_state["phase"] = "DAY"
    game_state["day"] += 1
    save_game_state()
    
    day_num_escaped = escape_markdown(game_state['day'])
    await context.bot.send_message(chat_id, rf"☀️ *Day {day_num_escaped} Has Begun*", parse_mode=ParseMode.MARKDOWN_V2)
    await asyncio.sleep(1)

    if game_state.get("murdered_last_night"):
        murdered_player = get_player_by_id(game_state, game_state["murdered_last_night"])
        if murdered_player:
            await context.bot.send_message(chat_id, rf"The Traitors acted in the night\. *{escape_markdown(murdered_player['name'])}* was murdered\!", parse_mode=ParseMode.MARKDOWN_V2)
        game_state["murdered_last_night"] = None
        save_game_state()
    else:
        await context.bot.send_message(chat_id, r"The Traitors did not act in the night\. No one was murdered\.")
    
    await asyncio.sleep(1)
    await display_team_scores(context, chat_id)
    await asyncio.sleep(1)
    await context.bot.send_message(chat_id, r"The discussion period has started\. The vote will begin automatically at the scheduled time\.")


async def start_night_phase(context: ContextTypes.DEFAULT_TYPE, chat_id):
    """Transitions the game to the night phase and prompts Traitors for action."""
    game_state = get_game_state(chat_id)
    if not game_state:
        return

    if await check_for_elimination_win(context, chat_id):
        return

    game_state["phase"] = "NIGHT"
    save_game_state()
    await context.bot.send_message(chat_id, r"🌙 *Night Falls*\nThe day's business is concluded\. Silence falls upon the group as the Traitors prepare to make their move\.", parse_mode=ParseMode.MARKDOWN_V2)

    active_traitors = [p for p in game_state["players"].values() if p["role"] == "Traitor" and p["status"] == "Active"]
    
    if not active_traitors:
        await context.bot.send_message(chat_id, r"All traitors have been banished\. The night passes peacefully\.")
        return

    traitor_to_prompt = random.choice(active_traitors)
    
    prompt_text = ""
    keyboard = []
    if len(active_traitors) >= 2:
        prompt_text = r"Traitors, it's time to act\. Will you Murder or attempt to Recruit\?"
        keyboard = [[InlineKeyboardButton("🔪 Murder a Faithful", callback_data="action_murder")], [InlineKeyboardButton("👤 Recruit a new member", callback_data="action_recruit")]]
    elif len(active_traitors) == 1:
        prompt_text = r"You are the last Traitor\. Will you Murder or Blackmail someone\?"
        keyboard = [[InlineKeyboardButton("🔪 Murder a Faithful", callback_data="action_murder")], [InlineKeyboardButton(" blackmail_victim", callback_data="action_blackmail")]]

    prompt_message = await send_private_message(context, traitor_to_prompt['id'], prompt_text, reply_markup=InlineKeyboardMarkup(keyboard))
    if prompt_message:
        game_state['active_night_prompt'] = {'user_id': traitor_to_prompt['id'], 'message_id': prompt_message.message_id}
        save_game_state()

async def schedule_night_phase(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Schedules the night phase to start after a 12-hour delay."""
    game_state = get_game_state(chat_id)
    if not game_state:
        return

    night_start_time = datetime.now(scheduler.timezone) + timedelta(hours=12)
    game_state['night_schedule'] = night_start_time.isoformat()
    save_game_state()
    
    scheduler.add_job(
        start_night_phase,
        'date',
        run_date=night_start_time,
        args=[context, chat_id],
        id=f"night_start_{chat_id}",
        replace_existing=True
    )
    
    await context.bot.send_message(
        chat_id,
        rf"The day's business is concluded\. The night will fall in 12 hours at approximately *{escape_markdown(night_start_time.strftime('%H:%M'))}*\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

# --- Command Handlers ---

async def startgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts a new game lobby in the chat."""
    chat_id = update.message.chat_id
    if get_game_state(chat_id):
        await update.message.reply_text(r"A game is already in progress in this chat\!")
        return

    games[str(chat_id)] = {"phase": "LOBBY", "players": {}, "day": 0, "admin": update.message.from_user.id, "murdered_last_night": None, "active_night_prompt": None}
    save_game_state()
    await update.message.reply_text(
        r"A new game of *Traitors \(Points Version\)* is starting\!\n\n"
        r"Type `/join` to enter the game\.\n"
        r"The game admin can type `/begin` to start the game once everyone is in\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows a player to join a game in the LOBBY phase."""
    chat_id = update.message.chat_id
    user = update.message.from_user
    game_state = get_game_state(chat_id)

    if not game_state or game_state["phase"] != "LOBBY":
        await update.message.reply_text(r"There is no game to join right now\.")
        return

    if user.id in game_state["players"]:
        await update.message.reply_text(r"You are already in the game\.")
        return

    game_state["players"][user.id] = {"id": user.id, "name": user.first_name, "role": None, "status": "Active", "points": 0}
    save_game_state()
    safe_name = escape_markdown(user.first_name)
    num_players = escape_markdown(len(game_state['players']))
    await update.message.reply_text(rf"{safe_name} has joined the game\! Current players: {num_players}", parse_mode=ParseMode.MARKDOWN_V2)

async def begin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the game, assigns roles and points, and begins the first Night."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    game_state = get_game_state(chat_id)

    if not game_state or game_state["phase"] != "LOBBY":
        await update.message.reply_text(r"The game cannot be started at this time\.")
        return

    if user_id != game_state["admin"]:
        await update.message.reply_text(r"Only the game admin can start the game\.")
        return
        
    if len(game_state["players"]) < 3:
        await update.message.reply_text(r"You need at least 3 players to start\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    player_ids = list(game_state["players"].keys())
    
    # Dynamic Traitor Assignment
    num_players = len(player_ids)
    if num_players <= 5:
        num_traitors = 1
    elif num_players <= 8:
        num_traitors = 2
    else:
        num_traitors = DEFAULT_NUM_TRAITORS
        
    traitor_ids = random.sample(player_ids, k=num_traitors)
    
    faithfuls = []
    traitors = []
    for pid in player_ids:
        player = game_state["players"][pid]
        if pid in traitor_ids:
            player["role"] = "Traitor"
            traitors.append(player)
        else:
            player["role"] = "Faithful"
            faithfuls.append(player)

    if faithfuls:
        points_per_faithful = TEAM_STARTING_POINTS // len(faithfuls)
        for p in faithfuls: p['points'] = points_per_faithful
    
    if traitors:
        points_per_traitor = TEAM_STARTING_POINTS // len(traitors)
        for p in traitors: p['points'] = points_per_traitor

    traitors_list_text = [escape_markdown(p['name']) for p in traitors]
    for player in game_state["players"].values():
        player_points_escaped = escape_markdown(player['points'])
        if player["role"] == "Traitor":
            await send_private_message(context, player['id'], rf"🤫 You are a *Traitor*\.\nYour fellow Traitors are: {', '.join(traitors_list_text)}\. You start with {player_points_escaped} points\.")
        else:
            await send_private_message(context, player['id'], rf"😇 You are a *Faithful*\.\nFind the traitors before they steal all the points\! You start with {player_points_escaped} points\.")

    await update.message.reply_text(r"The game is starting now\! Roles and points have been assigned privately\. The first night begins now\.", parse_mode=ParseMode.MARKDOWN_V2)
    await asyncio.sleep(2)
    await start_night_phase(context, chat_id)

async def initiate_vote_poll(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Helper function to create and send the voting poll."""
    game_state = get_game_state(chat_id)
    if not game_state or game_state.get("poll_message_id"):
        return

    active_players = [p for p in game_state["players"].values() if p["status"] == "Active"]
    
    keyboard = [[InlineKeyboardButton(player['name'], callback_data=f"vote_{player['id']}")] for player in active_players]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    poll_message = await context.bot.send_message(chat_id=chat_id, text=r"🗳️ *Cast your votes below\!*", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    game_state["poll_message_id"] = poll_message.message_id
    save_game_state()

async def startvote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows the admin to manually initiate the day and the voting poll."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    game_state = get_game_state(chat_id)

    if not game_state or game_state["phase"] not in ["NIGHT", "DAY"]:
        await update.message.reply_text(r"This command can only be used during the Day or Night phase\.")
        return

    if user_id != game_state.get("admin"):
        await update.message.reply_text(r"Only the game admin can start the day\.")
        return
    
    # Cancel any existing scheduled jobs for this game
    try:
        scheduler.remove_job(f"transition_{chat_id}")
        scheduler.remove_job(f"vote_{chat_id}")
        await update.message.reply_text(r"Overriding scheduled vote\.")
    except JobLookupError:
        pass # No job was scheduled, which is fine.

    await begin_day_phase(context, chat_id)

async def schedule_vote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Schedules the day and vote to start at a specific time."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    game_state = get_game_state(chat_id)

    if not game_state or game_state["phase"] != "NIGHT":
        await update.message.reply_text(r"The day can only be scheduled during the Night phase\.")
        return

    if user_id != game_state.get("admin"):
        await update.message.reply_text(r"Only the game admin can schedule the vote\.")
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text(r"Usage: `/schedule_vote HH:MM` \(24-hour format\)")
        return

    try:
        now = datetime.now(scheduler.timezone)
        hour, minute = map(int, context.args[0].split(':'))
        scheduled_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if scheduled_dt < now:
            scheduled_dt += timedelta(days=1)

        time_delta = scheduled_dt - now

        if time_delta > timedelta(hours=4):
            half_duration = time_delta / 2
            midpoint_dt = now + half_duration
            
            game_state['transition_schedule'] = midpoint_dt.isoformat()
            game_state['vote_schedule'] = scheduled_dt.isoformat()
            
            await update.message.reply_text(
                rf"⏰ *Timed Mode Activated\!* The vote is scheduled for *{escape_markdown(scheduled_dt.strftime('%H:%M'))}*\."
                rf"\n\n🌙 The Night Phase will last until *{escape_markdown(midpoint_dt.strftime('%H:%M'))}*\."
                rf"\n☀️ The Day/Discussion Phase will last from *{escape_markdown(midpoint_dt.strftime('%H:%M'))}* to *{escape_markdown(scheduled_dt.strftime('%H:%M'))}*\."
                rf"\n\nThe vote will begin automatically at the end of the Day Phase\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            scheduler.add_job(transition_to_day_discussion, 'date', run_date=midpoint_dt, args=[context, chat_id], id=f"transition_{chat_id}", replace_existing=True)
            scheduler.add_job(initiate_vote_poll, 'date', run_date=scheduled_dt, args=[context, chat_id], id=f"vote_{chat_id}", replace_existing=True)

        else:
            game_state['vote_schedule'] = scheduled_dt.isoformat()
            scheduler.add_job(begin_day_phase, 'date', run_date=scheduled_dt, args=[context, chat_id], id=f"vote_{chat_id}", replace_existing=True)
            await update.message.reply_text(rf"✅ The next Day Phase is scheduled for *{escape_markdown(scheduled_dt.strftime('%H:%M'))}*\.", parse_mode=ParseMode.MARKDOWN_V2)
        
        save_game_state()

    except (ValueError, TypeError):
        await update.message.reply_text(r"Invalid time format\. Please use `HH:MM`\.")

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows an admin to remove a player from the game."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    game_state = get_game_state(chat_id)

    if not game_state:
        await update.message.reply_text(r"There is no game in progress\.")
        return

    if user_id != game_state.get("admin"):
        await update.message.reply_text(r"Only the game admin can use this command\.")
        return

    if not context.args:
        await update.message.reply_text(r"Usage: `/remove [PlayerName]`")
        return

    target_name = " ".join(context.args)
    target_player = None
    for p in game_state["players"].values():
        if p["name"].lower() == target_name.lower() and p["status"] == "Active":
            target_player = p
            break

    if not target_player:
        await update.message.reply_text(rf"Could not find an active player named `{escape_markdown(target_name)}`\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    target_player["status"] = "Banished"
    
    await context.bot.send_message(chat_id, rf"🚨 The admin has removed *{escape_markdown(target_player['name'])}* from the game\." "\n" rf"They were a *{escape_markdown(target_player['role'])}*\.", parse_mode=ParseMode.MARKDOWN_V2)

    if target_player['role'] == 'Traitor':
        points_seized = target_player['points']
        target_player['points'] = 0
        active_faithful = [p for p in game_state['players'].values() if p['role'] == 'Faithful' and p['status'] == 'Active']
        if active_faithful:
            points_per_faithful = points_seized // len(active_faithful)
            for f in active_faithful: f['points'] += points_per_faithful
            await context.bot.send_message(chat_id, rf"💰 As a Traitor, {escape_markdown(target_player['name'])}'s *{escape_markdown(points_seized)} points* have been seized and distributed among the remaining Faithful\!", parse_mode=ParseMode.MARKDOWN_V2)
    elif target_player['role'] == 'Faithful':
        points_transferred = target_player['points']
        target_player['points'] = 0
        active_traitors = [p for p in game_state['players'].values() if p['role'] == 'Traitor' and p['status'] == 'Active']
        if active_traitors:
            points_per_traitor = points_transferred // len(active_traitors)
            for t in active_traitors: t['points'] += points_per_traitor
            await context.bot.send_message(chat_id, rf"💔 A Faithful has fallen\! {escape_markdown(target_player['name'])}'s *{escape_markdown(points_transferred)} points* have been transferred to the Traitors\!", parse_mode=ParseMode.MARKDOWN_V2)

    save_game_state()
    if await check_for_points_win(context, chat_id): return
    if await check_for_elimination_win(context, chat_id): return

async def endgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ends the game and tallies the final scores based on points."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    game_state = get_game_state(chat_id)

    if not game_state:
        await update.message.reply_text(r"No game is currently in progress\.")
        return

    if user_id != game_state.get("admin"):
        await update.message.reply_text(r"Only the game admin can end the game\.")
        return

    faithful_points = sum(p['points'] for p in game_state['players'].values() if p['role'] == 'Faithful')
    traitor_points = sum(p['points'] for p in game_state['players'].values() if p['role'] == 'Traitor')
    
    final_traitors = [escape_markdown(p['name']) for p in game_state["players"].values() if p['role'] == 'Traitor']
    
    winner = "Faithful" if faithful_points >= traitor_points else "Traitors"

    result_text = (
        rf"🏆 *The Game Has Ended\!* 🏆\n\n"
        rf"The final Traitors were: {', '.join(final_traitors)}\n\n"
        r"*Final Scores:*" "\n"
        rf"😇 Faithful Team: {escape_markdown(faithful_points)} points\n"
        rf"🤫 Traitor Team: {escape_markdown(traitor_points)} points\n\n"
        rf"🎉 The *{escape_markdown(winner)}* team wins by points\!"
    )
    await update.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN_V2)

    if str(chat_id) in games:
        del games[str(chat_id)]
    save_game_state()

async def display_team_scores(context: ContextTypes.DEFAULT_TYPE, chat_id):
    """Calculates and displays team scores."""
    game_state = get_game_state(chat_id)
    if not game_state: return
        
    faithful_team_points = sum(p['points'] for p in game_state['players'].values() if p['role'] == 'Faithful')
    traitor_team_points = sum(p['points'] for p in game_state['players'].values() if p['role'] == 'Traitor')
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=rf"📊 *Current Team Scores*\n"
             rf"😇 Faithful Team Total: *{escape_markdown(faithful_team_points)} points*\n"
             rf"🤫 Traitor Team Total: *{escape_markdown(traitor_team_points)} points*",
        parse_mode=ParseMode.MARKDOWN_V2
    )

# --- Callback Query Handler (for Inline Buttons) ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all button presses from inline keyboards."""
    query = update.callback_query
    user_id = query.from_user.id
    
    chat_id = None
    for cid, gs in games.items():
        if user_id in gs['players']:
            chat_id = int(cid)
            break
    if not chat_id:
        await query.answer("You are not in an active game.", show_alert=True)
        return

    game_state = get_game_state(chat_id)
    if not game_state: return

    player = get_player_by_id(game_state, user_id)
    if not player: return
    
    if player['status'] != 'Active' and not query.data.startswith(("recruit", "blackmail")):
        await query.answer("You are banished and cannot perform this action.", show_alert=True)
        return

    data = query.data.split('_')
    action = data[0]

    if action == "vote":
        target_id = int(data[1])
        
        if user_id == target_id:
            await query.answer("You cannot vote for yourself.", show_alert=True)
            return

        if user_id in game_state.get("votes", {}):
            await query.answer("You have already voted.", show_alert=True)
            return
        
        await query.answer("Your vote has been cast!")
        game_state.setdefault("votes", {})[user_id] = target_id
        save_game_state()
        
        vote_counts = {}
        for target in game_state["votes"].values():
            vote_counts[target] = vote_counts.get(target, 0) + 1
        
        active_players = [p for p in game_state["players"].values() if p["status"] == "Active"]
        new_keyboard = []
        for p in active_players:
            count = vote_counts.get(p['id'], 0)
            button_text = f"{p['name']} ({count} Votes)"
            button = InlineKeyboardButton(button_text, callback_data=f"vote_{p['id']}")
            new_keyboard.append([button])
        
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=game_state["poll_message_id"], reply_markup=InlineKeyboardMarkup(new_keyboard))

        active_players_count = sum(1 for p in game_state["players"].values() if p["status"] == "Active")
        if len(game_state["votes"]) == active_players_count:
            max_votes = max(vote_counts.values()) if vote_counts else 0
            banished_candidates = [pid for pid, count in vote_counts.items() if count == max_votes]
            
            await context.bot.edit_message_text(chat_id=chat_id, message_id=game_state["poll_message_id"], text=r"🗳️ *Voting has ended\!*", parse_mode=ParseMode.MARKDOWN_V2)

            if len(banished_candidates) != 1:
                await context.bot.send_message(chat_id, r"The vote was a tie\! No one is banished today\.", parse_mode=ParseMode.MARKDOWN_V2)
                await schedule_night_phase(context, chat_id)
            else:
                banished_id = banished_candidates[0]
                banished_player = get_player_by_id(game_state, banished_id)
                banished_player["status"] = "Banished"
                
                await context.bot.send_message(chat_id, rf"🗳️ The votes are in\! *{escape_markdown(banished_player['name'])}* has been banished\." "\n" rf"They were a *{escape_markdown(banished_player['role'])}*\.\n\n" r"Their points are now frozen\. They can no longer vote or be targeted\.", parse_mode=ParseMode.MARKDOWN_V2)

                if banished_player['role'] == 'Traitor':
                    points_seized = banished_player['points']
                    banished_player['points'] = 0
                    active_faithful = [p for p in game_state['players'].values() if p['role'] == 'Faithful' and p['status'] == 'Active']
                    if active_faithful:
                        points_per_faithful = points_seized // len(active_faithful)
                        for f in active_faithful: f['points'] += points_per_faithful
                        await context.bot.send_message(chat_id, rf"💰 As a Traitor, {escape_markdown(banished_player['name'])}'s *{escape_markdown(points_seized)} points* have been seized and distributed among the remaining Faithful\!", parse_mode=ParseMode.MARKDOWN_V2)
                elif banished_player['role'] == 'Faithful':
                    points_transferred = banished_player['points']
                    banished_player['points'] = 0
                    active_traitors = [p for p in game_state['players'].values() if p['role'] == 'Traitor' and p['status'] == 'Active']
                    if active_traitors:
                        points_per_traitor = points_transferred // len(active_traitors)
                        for t in active_traitors: t['points'] += points_per_traitor
                        await context.bot.send_message(chat_id, rf"💔 A Faithful has fallen\! {escape_markdown(banished_player['name'])}'s *{escape_markdown(points_transferred)} points* have been transferred to the Traitors\!", parse_mode=ParseMode.MARKDOWN_V2)
                
                await display_team_scores(context, chat_id)
                save_game_state()

                if await check_for_points_win(context, chat_id): return
                if await check_for_elimination_win(context, chat_id): return
                
                await schedule_night_phase(context, chat_id)

    elif action == "action":
        await query.answer()
        game_state['active_night_prompt'] = None # The action is being taken, so the prompt is no longer active
        save_game_state()
        choice = data[1]
        active_faithful = [p for p in game_state["players"].values() if p["role"] == "Faithful" and p["status"] == "Active"]
        
        if not active_faithful:
            await query.edit_message_text(r"There are no active Faithful to target\.")
            return

        keyboard = []
        if choice == "murder" or choice == "blackmail":
            prompt = r"Who will you murder\?" if choice == "murder" else r"Who will you blackmail\?"
            for faithful in active_faithful:
                button = InlineKeyboardButton(f"{faithful['name']} ({faithful['points']} pts)", callback_data=f"target_{choice}_{faithful['id']}")
                keyboard.append([button])
            await query.edit_message_text(text=prompt, reply_markup=InlineKeyboardMarkup(keyboard))
        
        elif choice == "recruit":
            prompt = r"Who will you offer recruitment to\?"
            for faithful in active_faithful:
                button = InlineKeyboardButton(f"{faithful['name']} ({faithful['points']} pts)", callback_data=f"target_recruit_{faithful['id']}")
                keyboard.append([button])
            await query.edit_message_text(text=prompt, reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "target":
        await query.answer()
        action_type = data[1]
        target_id = int(data[2])
        target_player = get_player_by_id(game_state, target_id)

        if action_type == "murder":
            points_to_transfer = target_player['points']
            target_player['points'] = 0
            target_player['status'] = 'Banished'
            
            active_traitors = [p for p in game_state["players"].values() if p["role"] == "Traitor" and p["status"] == "Active"]
            if active_traitors:
                points_per_traitor = points_to_transfer // len(active_traitors)
                for t in active_traitors: t["points"] += points_per_traitor
            
            game_state["murdered_last_night"] = target_player['id']
            await query.edit_message_text(text=rf"Action confirmed\. You have chosen to murder {escape_markdown(target_player['name'])}\. The result will be revealed at the start of the next day\.", parse_mode=ParseMode.MARKDOWN_V2)

        elif action_type == "recruit":
            await query.edit_message_text(text=rf"You have offered recruitment to {escape_markdown(target_player['name'])}\. They will now receive the offer\.", parse_mode=ParseMode.MARKDOWN_V2)
            keyboard = [[InlineKeyboardButton("Accept", callback_data=f"recruit_accept_{user_id}")], [InlineKeyboardButton("Decline", callback_data=f"recruit_decline_{user_id}")]]
            await send_private_message(context, target_id, r"🤫 You have been secretly offered to join the Traitors\. Do you accept\?", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action_type == "blackmail":
             await query.edit_message_text(text=rf"You have chosen to blackmail {escape_markdown(target_player['name'])}\. They will now receive the ultimatum\.", parse_mode=ParseMode.MARKDOWN_V2)
             keyboard = [[InlineKeyboardButton("Become a Traitor", callback_data=f"blackmail_accept_{user_id}")], [InlineKeyboardButton(f"Lose {BLACKMAIL_STEAL_AMOUNT} points", callback_data=f"blackmail_decline_{user_id}")]]
             await send_private_message(context, target_id, rf"You have been blackmailed\! The last Traitor gives you a choice: become a Traitor or lose {escape_markdown(BLACKMAIL_STEAL_AMOUNT)} points\.", reply_markup=InlineKeyboardMarkup(keyboard))
        
        save_game_state()

    elif action == "recruit" or action == "blackmail":
        await query.answer()
        decision = data[1]
        original_traitor_id = int(data[2])

        if decision == "accept":
            player["role"] = "Traitor"
            await query.edit_message_text(text=r"You have accepted and are now a Traitor\!")
            await send_private_message(context, original_traitor_id, rf"{escape_markdown(player['name'])} has accepted the offer and is now a Traitor\.")
        
        elif decision == "decline":
            if action == "recruit":
                await query.edit_message_text(text=r"You have declined the offer\. You remain a Faithful\.")
                await send_private_message(context, original_traitor_id, rf"{escape_markdown(player['name'])} has declined the offer\.")
            else:
                player["points"] -= BLACKMAIL_STEAL_AMOUNT
                traitor = get_player_by_id(game_state, original_traitor_id)
                traitor["points"] += BLACKMAIL_STEAL_AMOUNT
                await query.edit_message_text(text=rf"You have chosen to lose {escape_markdown(BLACKMAIL_STEAL_AMOUNT)} points\.")
                await send_private_message(context, original_traitor_id, rf"{escape_markdown(player['name'])} chose to lose points instead of joining you\.")
                
                if await check_for_points_win(context, chat_id): return
        
        save_game_state()
        await context.bot.send_message(chat_id, r"The Traitors have completed their business for the night\.")

# --- Main Function to Run the Bot ---

async def post_init(application: Application):
    """Post-initialization function to start the scheduler and load state."""
    load_game_state()
    context = ContextTypes.DEFAULT_TYPE(application=application)
    
    # Reschedule any pending jobs from the loaded state
    now = datetime.now(scheduler.timezone)
    for chat_id_str, game_state in games.items():
        chat_id = int(chat_id_str)
        if game_state.get('vote_schedule'):
            run_time = datetime.fromisoformat(game_state['vote_schedule'])
            if run_time > now:
                scheduler.add_job(begin_day_phase, 'date', run_date=run_time, args=[context, chat_id], id=f"vote_{chat_id}", replace_existing=True)
        
        if game_state.get('transition_schedule'):
            run_time = datetime.fromisoformat(game_state['transition_schedule'])
            if run_time > now:
                scheduler.add_job(transition_to_day_discussion, 'date', run_date=run_time, args=[context, chat_id], id=f"transition_{chat_id}", replace_existing=True)
        
        if game_state.get('night_schedule'):
            run_time = datetime.fromisoformat(game_state['night_schedule'])
            if run_time > now:
                scheduler.add_job(start_night_phase, 'date', run_date=run_time, args=[context, chat_id], id=f"night_start_{chat_id}", replace_existing=True)

    scheduler.start()

def main():
    """Start the bot."""
    # Increase the timeout to 30 seconds to prevent TimedOut errors
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(30)
        .read_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("startgame", startgame_command))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CommandHandler("begin", begin_command))
    application.add_handler(CommandHandler("startvote", startvote_command))
    application.add_handler(CommandHandler("schedule_vote", schedule_vote_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("endgame", endgame_command))

    application.add_handler(CallbackQueryHandler(button_callback))
    
    application.run_polling()

if __name__ == "__main__":
    main()
