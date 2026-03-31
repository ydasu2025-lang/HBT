import threading
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import random
import time
import os
import traceback
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask

app = Flask("")

@app.route("/")
def home():
    return "Bot is running"

def run():
    port = int(os.environ["PORT"])
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def keep_alive():
    t = threading.Thread(target=run, daemon=True)
    t.start()

TOKEN = os.getenv("TOKEN")
print("TOKEN exists:", bool(TOKEN))

JST = ZoneInfo("Asia/Tokyo")

# ログ用チャンネルID
GACHA_LOG_CHANNEL_ID = 1487008334358773842

# ここを設定
INSTANT_IMAGE_CHANNEL_ID = 1487497896000618507
STEAL_CHANNEL_ID = 1487497933632045276

# リンクを許可するチャンネルID
ALLOWED_LINK_CHANNEL_IDS = [
    STEAL_CHANNEL_ID,
    1486779110758940853,
    1486877578093658162,
    1486844371340099646,
    1487129297125642453,
    1486767380054016042,
    1486768643445489815,
    1486770075896905859,
    1486778385194811656,
    1486992052934807604,
    1487135231273337034,
    1486774516595032188,
    1486775935570022501,
    1487502545357111549,
    1486842434666369105,
    1487125777743872061,
    1487428176148693134,
    1487135455261757533,
    1487337446189563934
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = "/var/data/data.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.Lock()

recent_action_lock = threading.Lock()
recent_actions = {}
ACTION_DEDUP_SECONDS = 3.0

# 一瞬ch / 奪ったch 用
instant_posts_lock = threading.Lock()
latest_instant_post = None
INSTANT_DELETE_SECONDS = 15
STEAL_WINDOW_SECONDS = 25
INSTANT_REWARD = 10
STEAL_REWARD = 10

LINK_PATTERNS = [
    r"https?://",
    r"www\.",
    r"discord\.gg/",
    r"discord\.com/invite/"
]

with db_lock:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        coins INTEGER DEFAULT 0,
        last_post REAL DEFAULT 0
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS gacha_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        gacha_id TEXT NOT NULL,
        gacha_name TEXT NOT NULL,
        gacha_type TEXT NOT NULL,
        character_name TEXT NOT NULL,
        rarity TEXT NOT NULL,
        created_at REAL NOT NULL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS completion_rewards (
        gacha_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        awarded_at REAL NOT NULL,
        PRIMARY KEY (gacha_id, user_id)
    )
    """)

    conn.commit()

def check_and_mark_recent_action(user_id: int, action_name: str) -> bool:
    now = time.time()
    key = (user_id, action_name)

    with recent_action_lock:
        old_keys = [k for k, ts in recent_actions.items() if now - ts > ACTION_DEDUP_SECONDS]
        for k in old_keys:
            recent_actions.pop(k, None)

        last_ts = recent_actions.get(key)
        if last_ts is not None and now - last_ts < ACTION_DEDUP_SECONDS:
            return False

        recent_actions[key] = now
        return True

def get_user(user_id: int):
    with db_lock:
        row = conn.execute(
            "SELECT coins, last_post FROM users WHERE user_id=?",
            (str(user_id),)
        ).fetchone()

        if not row:
            conn.execute(
                "INSERT INTO users (user_id, coins, last_post) VALUES (?, 0, 0)",
                (str(user_id),)
            )
            conn.commit()
            return 0, 0

        return row

def add_coins(user_id: int, amount: int):
    coins, _ = get_user(user_id)
    new_amount = max(0, coins + amount)
    with db_lock:
        conn.execute(
            "UPDATE users SET coins=? WHERE user_id=?",
            (new_amount, str(user_id))
        )
        conn.commit()

def set_last_post(user_id: int):
    with db_lock:
        conn.execute(
            "UPDATE users SET last_post=? WHERE user_id=?",
            (time.time(), str(user_id))
        )
        conn.commit()

def log_gacha(user_id: int, gacha_id: str, gacha_name: str, gacha_type: str, character_name: str, rarity: str):
    with db_lock:
        conn.execute(
            """
            INSERT INTO gacha_logs (user_id, gacha_id, gacha_name, gacha_type, character_name, rarity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(user_id), gacha_id, gacha_name, gacha_type, character_name, rarity, time.time())
        )
        conn.commit()

def add_completion_reward_record(gacha_id: str, user_id: int):
    with db_lock:
        conn.execute(
            """
            INSERT INTO completion_rewards (gacha_id, user_id, awarded_at)
            VALUES (?, ?, ?)
            ON CONFLICT(gacha_id, user_id) DO NOTHING
            """,
            (gacha_id, str(user_id), time.time())
        )
        conn.commit()

def has_completion_reward_record(gacha_id: str, user_id: int) -> bool:
    with db_lock:
        row = conn.execute(
            "SELECT 1 FROM completion_rewards WHERE gacha_id=? AND user_id=?",
            (gacha_id, str(user_id))
        ).fetchone()
    return row is not None

def remove_completion_reward_record(gacha_id: str, user_id: int):
    with db_lock:
        conn.execute(
            "DELETE FROM completion_rewards WHERE gacha_id=? AND user_id=?",
            (gacha_id, str(user_id))
        )
        conn.commit()

def has_disallowed_link(text: str) -> bool:
    if not text:
        return False
    for pattern in LINK_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False

def is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = attachment.content_type or ""
    filename = attachment.filename.lower()
    image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp")

    if content_type.startswith("image/"):
        return True
    if filename.endswith(image_exts):
        return True
    return False

ALLOWED_CHANNEL_IDS = [
    1486774240735789066,
    1486774297505824810,
    1486774309018931240,
    1486774516595032188,
    1486775878502584360,
    1486877578093658162
]

ALLOWED_COMMAND_CHANNELS = [
    1486779110758940853,
    1486877578093658162
]

COOLDOWN_SECONDS = 2
TRADE_COST = 350

CHARACTER_MAP = {
    "char_001" : "[001]みゆ",
    "char_002" : "[002]りみ",
    "char_003" : "[003]さえ",
    "char_004" : "[004]ふうあ",
    "char_005" : "[005]そら",
    "char_006" : "[006]せりな",
    "char_007" : "[007]せな",
    "char_008" : "[008]ゆうな",
    "char_009" : "[009]ここな",
    "char_010" : "[010]みう",
    "char_011" : "[011]いずみ",
    "char_012" : "[012]りの",
    "char_013" : "[013]えこ",
    "char_014" : "[014]ひめの",
    "char_015" : "[015]こころ",
    "char_016" : "[016]あん",
    "char_017" : "[017]れんか",
    "char_018" : "[018]ここ",
    "char_019" : "[019]りおな",
    "char_020" : "[020]まさき",


}

WEEKLY_GACHAS = [
    {
        "id": "normal_2026_w13",
        "name": "通常ガチャ 3月4週目",
        "type": "normal",
        "start": "2026-03-23 00:00",
        "end": "2026-03-31 23:59",
        "role_id": 1487114322781143081,
        "cost": 50,
        "items": [
            ("[001]みゆ", "S", 8, "https://cdn.discordapp.com/attachments/1486776583858425911/1486834490017055010/S.png?ex=69c6f206&is=69c5a086&hm=69ea5c80115bc07d31794aeefad633b5a099eb68336ce3fa79ff63ba8ac83f22&"),
            ("[002]りみ", "S", 8, "https://cdn.discordapp.com/attachments/1486776583858425911/1486840365032935446/S_1.png?ex=69c6f77f&is=69c5a5ff&hm=260cee99ea95db450ce7dc8a71308bb0ae6bc04a0eff5412cae66247c91b5d7d&"),
            ("[003]さえ", "S", 8, "https://cdn.discordapp.com/attachments/1486863251525603478/1486871101710663770/S_3.png?ex=69c7141f&is=69c5c29f&hm=86c5c46921441db38ca19811c6e693c90fb0227b2046e85f84467e97553c0d2a&"),
            ("[004]ふうあ", "S", 8, "https://cdn.discordapp.com/attachments/1486863251525603478/1486876158749315155/S_2.png?ex=69c718d5&is=69c5c755&hm=4c361ef76e57c20fc3d8bc99484613366ee32455ecaf2184e6df3c0f36062542&"),
            ("[005]そら", "S", 8, "https://cdn.discordapp.com/attachments/1487010239650988182/1487012914228494365/S_3.png?ex=69c79832&is=69c646b2&hm=a6be733f8bbed7cb41feff01e5e2774b34b6b53d39e7a929db134cc030a65740&"),
            ("[006]せりな", "S", 8, "https://cdn.discordapp.com/attachments/1486776583858425911/1487110091193843783/S_4.png?ex=69c7f2b2&is=69c6a132&hm=7fce72b32da9c4c844d180a57f25ca616c9f06ee412ccee39955cb45e09a7973&"),
            ("[007]せな", "S", 8, "https://cdn.discordapp.com/attachments/1487059067254870078/1487112005620858900/S_5.png?ex=69c7f47b&is=69c6a2fb&hm=0f71015b6d6386cf0a402aa910c3849045672e525133428a9736347b6f1145f2&"),
            ("[008]ゆうな", "S", 8, "https://cdn.discordapp.com/attachments/1487131651677880480/1487142082178191500/S_6.png?ex=69c8107e&is=69c6befe&hm=d2bc038c8ba07020cb47dcb1ad8e2cf2599a0ee4f55dfe370fb71b10bfc4e9ed&"),
            ("[009]ここな", "B", 21, "https://cdn.discordapp.com/attachments/1486776583858425911/1487150046339141704/image.png?ex=69c817e8&is=69c6c668&hm=b8eb1f263fa314d015aa16ce7fffda080c484790748fd67eed12f448ba9f381f&"),
            ("[010]みう", "A", 15, "https://cdn.discordapp.com/attachments/1486776583858425911/1487150273628733522/image.png?ex=69c8181f&is=69c6c69f&hm=3cbbafe3e43be3970fb7cde3f10d4670acb7eb8c699396cf069ba789e00417ab&")
        ]
    },
    {
        "id": "normal_2026_w14",
        "name": "通常ガチャ 4月1週目",
        "type": "normal",
        "start": "2026-04-01 00:00",
        "end": "2026-04-07 00:00",
        "role_id": 1487365710035292252,
        "cost": 50,
        "items": [
            ("[011]いずみ", "S", 6, "https://cdn.discordapp.com/attachments/1486776583858425911/1487897175789797396/S_8.png?ex=69cacfba&is=69c97e3a&hm=219090068ff92b4d6459dbd8c9ff969f4a8f31b57f30285652b81fa2d408453a&"),
            ("[012]りの", "S", 6, "https://cdn.discordapp.com/attachments/1486776583858425911/1487897331582898236/S_10.png?ex=69cacfdf&is=69c97e5f&hm=224bda5c89e807ba08a53cf3f880cd13bbe167e3a23679392deb9c8bb39d7fc2&"),
            ("[013]えこ", "A", 11, "https://cdn.discordapp.com/attachments/1486776583858425911/1487897590103019721/S_11.png?ex=69cad01d&is=69c97e9d&hm=5fd421c5252037ef0a97f0a4a8a8b67adbee25ef6565f43ffbec8489cf402811&"),
            ("[014]ひめの", "A", 11, "https://cdn.discordapp.com/attachments/1486776583858425911/1487897733221060780/S_24.png?ex=69cad03f&is=69c97ebf&hm=efd6fa1f1cf2a9808ab7f4c93cdf9e0ee2b0f6151dbebd31832b3262e695769d&"),
            ("[015]こころ", "A", 11, "https://cdn.discordapp.com/attachments/1486776583858425911/1487897907909623951/S_15.png?ex=69cad069&is=69c97ee9&hm=42079b6dd381940b765a3f522f726c9d1cdde7983f5eec9daa12200dd223e8b9&"),
            ("[016]あん", "B", 16, "https://cdn.discordapp.com/attachments/1486776583858425911/1487898056857751662/S_23.png?ex=69cad08c&is=69c97f0c&hm=7bb0b8e4e2ec277987b1fc8078c4236d8b78497bb541f8274431cbc7576563c7&"),
            ("[017]れんか", "A", 11, "https://cdn.discordapp.com/attachments/1486776583858425911/1487898221652086925/S_18.png?ex=69cad0b3&is=69c97f33&hm=00827121c56418dc18197781ec1bd1a54c929e5c98d89018281a2d79865f51ab&"),
            ("[018]ここ", "A", 11, "https://cdn.discordapp.com/attachments/1486776583858425911/1487898541052395664/S_19.png?ex=69cad100&is=69c97f80&hm=2ddf961e77d38564f583b26fb5109606ba208dde146b6e5eb5247a74f9f94ced&"),
            ("[019]りおな", "S", 6, "https://cdn.discordapp.com/attachments/1486776583858425911/1487898695621017620/S_20.png?ex=69cad124&is=69c97fa4&hm=2df05b28aab82223a47429328d2a137b13540bac23d923696c29329490caca06&"),
            ("[020]まさき", "A", 11, "https://cdn.discordapp.com/attachments/1486776583858425911/1487898828748095711/S_21.png?ex=69cad144&is=69c97fc4&hm=24c257abd5ed1b65ae7ebd2772e247b0ecbf3b0e4d8acf82817ee039f2bcc840&")
        ]
    }
]

LIMITED_GACHAS = []

def now_jst():
    return datetime.now(JST)

def parse_jst(dt_str: str) -> datetime:
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=JST)

def is_gacha_active(gacha_def: dict) -> bool:
    now = now_jst()
    return parse_jst(gacha_def["start"]) <= now < parse_jst(gacha_def["end"])

def get_active_weekly_gacha():
    for g in WEEKLY_GACHAS:
        if is_gacha_active(g):
            return g
    return None

def get_active_limited_gachas():
    return [g for g in LIMITED_GACHAS if is_gacha_active(g)]

def get_limited_gacha_by_id(gacha_id: str):
    for g in LIMITED_GACHAS:
        if g["id"] == gacha_id and is_gacha_active(g):
            return g
    return None

def roll_from_items(items):
    total = sum(item[2] for item in items)
    r = random.randint(1, total)
    current = 0
    for name, rarity, weight, img in items:
        current += weight
        if r <= current:
            return name, rarity, img
    return items[-1]

def get_gacha_unique_total(gacha_def: dict) -> int:
    return len({item[0] for item in gacha_def["items"]})

def get_user_unique_count_for_gacha(user_id: int, gacha_id: str) -> int:
    with db_lock:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT character_name)
            FROM gacha_logs
            WHERE user_id=? AND gacha_id=?
            """,
            (str(user_id), gacha_id)
        ).fetchone()
    return row[0] if row and row[0] else 0

def get_missing_characters_for_gacha(user_id: int, gacha_def: dict):
    with db_lock:
        rows = conn.execute(
            """
            SELECT DISTINCT character_name
            FROM gacha_logs
            WHERE user_id=? AND gacha_id=?
            """,
            (str(user_id), gacha_def["id"])
        ).fetchall()

    owned = {row[0] for row in rows}
    all_chars = [item[0] for item in gacha_def["items"]]
    return [name for name in all_chars if name not in owned]

def has_character_for_gacha(user_id: int, gacha_id: str, character_name: str) -> bool:
    with db_lock:
        row = conn.execute(
            """
            SELECT 1
            FROM gacha_logs
            WHERE user_id=? AND gacha_id=? AND character_name=?
            LIMIT 1
            """,
            (str(user_id), gacha_id, character_name)
        ).fetchone()
    return row is not None

def find_item_in_gacha(gacha_def: dict, character_name: str):
    for item in gacha_def["items"]:
        name, rarity, weight, img = item
        if name == character_name:
            return item
    return None

async def trade_normal_character_autocomplete(
    interaction: discord.Interaction,
    current: str
):
    gacha_def = get_active_weekly_gacha()
    if gacha_def is None:
        return []

    user_id = interaction.user.id
    choices = []

    for name, rarity, weight, img in gacha_def["items"]:
        if has_character_for_gacha(user_id, gacha_def["id"], name):
            continue
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=name, value=name))

    return choices[:25]

async def send_gacha_log(
    user: discord.Member | discord.User,
    gacha_def: dict,
    character_name: str,
    remaining_count: int,
    action: str
):
    channel = bot.get_channel(GACHA_LOG_CHANNEL_ID)
    if channel is None:
        return

    now_text = datetime.now(JST).strftime("%m/%d %H:%M")

    if remaining_count <= 0:
        remain_text = "コンプ"
    else:
        remain_text = f"残{remaining_count}"

    gacha_name = gacha_def["name"].replace("通常ガチャ ", "").replace("限定ガチャ ", "")

    try:
        await channel.send(
            f"`{now_text}` {user.display_name}｜{gacha_name}｜{character_name}｜{action}｜{remain_text}"
        )
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

async def award_completion_role_if_needed(interaction_or_member, gacha_def: dict):
    role_id = gacha_def.get("role_id", 0)
    if not role_id:
        return None

    member = interaction_or_member if isinstance(interaction_or_member, discord.Member) else interaction_or_member.guild.get_member(interaction_or_member.user.id)
    if member is None:
        return None

    total_needed = get_gacha_unique_total(gacha_def)
    owned_count = get_user_unique_count_for_gacha(member.id, gacha_def["id"])

    if owned_count < total_needed:
        return None

    role = member.guild.get_role(role_id)
    if role is None:
        return None

    if role not in member.roles:
        try:
            await member.add_roles(role, reason=f"{gacha_def['name']} コンプ報酬")
        except discord.Forbidden:
            return None
        except discord.HTTPException:
            return None

    if not has_completion_reward_record(gacha_def["id"], member.id):
        add_completion_reward_record(gacha_def["id"], member.id)
        return role

    return None

async def remove_expired_completion_roles():
    all_gachas = WEEKLY_GACHAS + LIMITED_GACHAS
    active_ids = {g["id"] for g in all_gachas if is_gacha_active(g)}
    gacha_map = {g["id"]: g for g in all_gachas}

    with db_lock:
        reward_rows = conn.execute(
            "SELECT gacha_id, user_id FROM completion_rewards"
        ).fetchall()

    for gacha_id, user_id in reward_rows:
        if gacha_id in active_ids:
            continue

        gacha_def = gacha_map.get(gacha_id)
        if not gacha_def:
            remove_completion_reward_record(gacha_id, int(user_id))
            continue

        role_id = gacha_def.get("role_id", 0)
        if not role_id:
            remove_completion_reward_record(gacha_id, int(user_id))
            continue

        for guild in bot.guilds:
            member = guild.get_member(int(user_id))
            role = guild.get_role(role_id)
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=f"{gacha_def['name']} 期間終了")
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

        remove_completion_reward_record(gacha_id, int(user_id))

@tasks.loop(minutes=25)
async def periodic_cleanup():
    await remove_expired_completion_roles()

async def handle_instant_channel(message: discord.Message):
    global latest_instant_post

    attachments = message.attachments

    # 画像1枚のみ / 文章なし
    if len(attachments) != 1 or not is_image_attachment(attachments[0]) or message.content.strip():
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        try:
            warn = await message.channel.send("ここは画像1枚のみ送信できます。")
            await warn.delete(delay=5)
        except discord.HTTPException:
            pass
        return

    add_coins(message.author.id, INSTANT_REWARD)

    with instant_posts_lock:
        latest_instant_post = {
            "message_id": message.id,
            "author_id": message.author.id,
            "created_at": time.time(),
            "stolen": False
        }

    try:
        await message.delete(delay=INSTANT_DELETE_SECONDS)
    except discord.HTTPException:
        pass

async def handle_steal_channel(message: discord.Message):
    global latest_instant_post

    # 文章必須
    if not message.content.strip():
        return

    # 画像必須
    has_image = any(is_image_attachment(att) for att in message.attachments)
    if not has_image:
        return

    now = time.time()

    with instant_posts_lock:
        data = latest_instant_post

        if data is None:
            return

        if now - data["created_at"] > STEAL_WINDOW_SECONDS:
            return

        if data["stolen"]:
            return

        if data["author_id"] == message.author.id:
            return

        data["stolen"] = True

    add_coins(message.author.id, STEAL_REWARD)

    try:
        await message.add_reaction("💰")
    except Exception:
        pass

@bot.event
async def on_ready():
    migrate_gacha_logs_to_user_characters()

    try:
        guild = discord.Object(id=1486767379253035150)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Guild synced {len(synced)} commands")
    except Exception as e:
        print(f"Sync error: {e}")

    try:
        await remove_expired_completion_roles()
    except Exception as e:
        print(f"Initial role cleanup error: {e}")

    if not periodic_cleanup.is_running():
        periodic_cleanup.start()

    print(f"Logged in as {bot.user} ({bot.user.id})")
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.id not in ALLOWED_LINK_CHANNEL_IDS and has_disallowed_link(message.content):
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        try:
            warn = await message.channel.send("リンクは指定チャンネル以外では送信できません。")
            await warn.delete(delay=5)
        except discord.HTTPException:
            pass
        return

    if message.channel.id == INSTANT_IMAGE_CHANNEL_ID:
        await handle_instant_channel(message)
        await bot.process_commands(message)
        return

    if message.channel.id == STEAL_CHANNEL_ID:
        await handle_steal_channel(message)
        await bot.process_commands(message)
        return

    if message.channel.id not in ALLOWED_CHANNEL_IDS:
        await bot.process_commands(message)
        return

    allowed_exts = (
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".mp4", ".mov", ".webm", ".mkv"
    )

    media_count = 0

    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        filename = attachment.filename.lower()

        if content_type.startswith("image/") or content_type.startswith("video/"):
            media_count += 1
        elif filename.endswith(allowed_exts):
            media_count += 1

    if media_count > 0:
        _, last = get_user(message.author.id)
        if time.time() - last > COOLDOWN_SECONDS:
            add_coins(message.author.id, media_count * 25)
            set_last_post(message.author.id)

    await bot.process_commands(message)

@bot.tree.command(name="balance", description="自分のHPTを見る")
async def balance(interaction: discord.Interaction):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    coins, _ = get_user(interaction.user.id)
    await interaction.response.send_message(
        f"💰 {interaction.user.display_name} のHPT: {coins}",
        ephemeral=True
    )

@bot.tree.command(name="gacha", description="通常ガチャを引く")
async def gacha(interaction: discord.Interaction):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    if not check_and_mark_recent_action(interaction.user.id, "gacha"):
        await interaction.response.send_message(
            "処理中です。少し待ってからもう一度試してください。",
            ephemeral=True
        )
        return

    gacha_def = get_active_weekly_gacha()
    if gacha_def is None:
        await interaction.response.send_message(
            "現在開催中の通常ガチャはありません。",
            ephemeral=True
        )
        return

    coins, _ = get_user(interaction.user.id)
    cost = gacha_def.get("cost", 50)

    if coins < cost:
        await interaction.response.send_message(
            f"HPTが足りない！今は {coins} HPT、{cost}HPT必要、チャンネルに画像を投稿してHPTをGET",
            ephemeral=True
        )
        return

    add_coins(interaction.user.id, -cost)

    name, rarity, img = roll_from_items(gacha_def["items"])
    log_gacha(interaction.user.id, gacha_def["id"], gacha_def["name"], gacha_def["type"], name, rarity)

    missing = get_missing_characters_for_gacha(interaction.user.id, gacha_def)
    complete_role = await award_completion_role_if_needed(interaction, gacha_def)

    await send_gacha_log(interaction.user, gacha_def, name, len(missing), "ガチャ")

    embed = discord.Embed(
        title=f"🎰 {gacha_def['name']} 結果",
        description=f"{rarity}\n**{name}**",
        color=0xFFD700
    )
    embed.set_image(url=img)
    embed.set_footer(text=f"{cost}HPT消費しました")

    if complete_role:
        embed.add_field(
            name="🎉 コンプ達成",
            value=f"{gacha_def['name']} をコンプしました！\nロール「{complete_role.name}」を付与しました。",
            inline=False
        )
    else:
        if missing:
            embed.add_field(name="📘 コンプ状況", value=f"残り {len(missing)}種", inline=False)
        else:
            embed.add_field(name="📘 コンプ状況", value="コンプ済み", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="trade_normal", description="通常ガチャの未所持キャラを350HPTで交換")
@app_commands.describe(character="交換したいキャラ")
@app_commands.autocomplete(character=trade_normal_character_autocomplete)
async def trade_normal(interaction: discord.Interaction, character: str):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    if not check_and_mark_recent_action(interaction.user.id, "trade_normal"):
        await interaction.response.send_message(
            "処理中です。少し待ってからもう一度試してください。",
            ephemeral=True
        )
        return

    gacha_def = get_active_weekly_gacha()
    if gacha_def is None:
        await interaction.response.send_message(
            "現在開催中の通常ガチャはありません。",
            ephemeral=True
        )
        return

    item = find_item_in_gacha(gacha_def, character)
    if item is None:
        await interaction.response.send_message(
            "そのキャラは今回の通常ガチャにいません。",
            ephemeral=True
        )
        return

    if has_character_for_gacha(interaction.user.id, gacha_def["id"], character):
        await interaction.response.send_message(
            "そのキャラはすでに所持しています。未所持キャラのみ交換できます。",
            ephemeral=True
        )
        return

    coins, _ = get_user(interaction.user.id)
    if coins < TRADE_COST:
        await interaction.response.send_message(
            f"HPTが足りない！今は {coins} HPT、{TRADE_COST}HPT必要です。",
            ephemeral=True
        )
        return

    add_coins(interaction.user.id, -TRADE_COST)

    name, rarity, weight, img = item
    log_gacha(interaction.user.id, gacha_def["id"], gacha_def["name"], gacha_def["type"], name, rarity)

    missing = get_missing_characters_for_gacha(interaction.user.id, gacha_def)
    complete_role = await award_completion_role_if_needed(interaction, gacha_def)

    await send_gacha_log(interaction.user, gacha_def, name, len(missing), "交換")

    embed = discord.Embed(
        title=f"🛒 {gacha_def['name']} 交換結果",
        description=f"**{name}** を交換しました",
        color=0x66FF99
    )
    embed.set_image(url=img)
    embed.set_footer(text=f"{TRADE_COST}HPT消費しました")

    if complete_role:
        embed.add_field(
            name="🎉 コンプ達成",
            value=f"{gacha_def['name']} をコンプしました！\nロール「{complete_role.name}」を付与しました。",
            inline=False
        )
    else:
        if missing:
            embed.add_field(name="📘 コンプ状況", value=f"残り {len(missing)}種", inline=False)
        else:
            embed.add_field(name="📘 コンプ状況", value="コンプ済み", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="limitedgacha", description="限定ガチャを引く")
@app_commands.describe(event_id="限定ガチャID。1つしか開催中でない時は空欄でOK")
async def limitedgacha(interaction: discord.Interaction, event_id: str | None = None):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    if not check_and_mark_recent_action(interaction.user.id, "limitedgacha"):
        await interaction.response.send_message(
            "処理中です。少し待ってからもう一度試してください。",
            ephemeral=True
        )
        return

    active_limited = get_active_limited_gachas()
    if not active_limited:
        await interaction.response.send_message(
            "現在開催中の限定ガチャはありません。",
            ephemeral=True
        )
        return

    if event_id:
        gacha_def = get_limited_gacha_by_id(event_id)
        if gacha_def is None:
            active_text = "\n".join([f"- {g['id']} : {g['name']}" for g in active_limited])
            await interaction.response.send_message(
                f"その限定ガチャIDは使えません。\n開催中:\n{active_text}",
                ephemeral=True
            )
            return
    else:
        if len(active_limited) == 1:
            gacha_def = active_limited[0]
        else:
            active_text = "\n".join([f"- {g['id']} : {g['name']}" for g in active_limited])
            await interaction.response.send_message(
                f"開催中の限定ガチャが複数あります。event_id を指定してください。\n{active_text}",
                ephemeral=True
            )
            return

    coins, _ = get_user(interaction.user.id)
    cost = gacha_def.get("cost", 50)

    if coins < cost:
        await interaction.response.send_message(
            f"HPTが足りない！今は {coins} HPT、{cost}HPT必要です。",
            ephemeral=True
        )
        return

    add_coins(interaction.user.id, -cost)

    name, rarity, img = roll_from_items(gacha_def["items"])
    log_gacha(interaction.user.id, gacha_def["id"], gacha_def["name"], gacha_def["type"], name, rarity)

    missing = get_missing_characters_for_gacha(interaction.user.id, gacha_def)
    complete_role = await award_completion_role_if_needed(interaction, gacha_def)

    await send_gacha_log(interaction.user, gacha_def, name, len(missing), "ガチャ")

    embed = discord.Embed(
        title=f"🎰 {gacha_def['name']} 結果",
        description=f"{rarity}\n**{name}**",
        color=0xFF66CC
    )
    embed.set_image(url=img)
    embed.set_footer(text=f"{cost}HPT消費しました")

    if complete_role:
        embed.add_field(
            name="🎉 コンプ達成",
            value=f"{gacha_def['name']} をコンプしました！\nロール「{complete_role.name}」を付与しました。",
            inline=False
        )
    else:
        if missing:
            embed.add_field(name="📘 コンプ状況", value=f"残り {len(missing)}種", inline=False)
        else:
            embed.add_field(name="📘 コンプ状況", value="コンプ済み", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="limitedlist", description="開催中の限定ガチャ一覧を見る")
async def limitedlist(interaction: discord.Interaction):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    active_limited = get_active_limited_gachas()
    if not active_limited:
        await interaction.response.send_message(
            "現在開催中の限定ガチャはありません。",
            ephemeral=True
        )
        return

    lines = []
    for g in active_limited:
        lines.append(f"ID: {g['id']}")
        lines.append(f"名前: {g['name']}")
        lines.append(f"期間: {g['start']} ～ {g['end']}")
        lines.append("")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="collection", description="自分のコンプ状況を見る")
@app_commands.describe(gacha_type="normal または limited", event_id="limited の時だけ必要")
async def collection(interaction: discord.Interaction, gacha_type: str, event_id: str | None = None):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    gacha_type = gacha_type.lower().strip()

    if gacha_type == "normal":
        gacha_def = get_active_weekly_gacha()
        if gacha_def is None:
            await interaction.response.send_message(
                "現在開催中の通常ガチャはありません。",
                ephemeral=True
            )
            return
    elif gacha_type == "limited":
        if not event_id:
            await interaction.response.send_message(
                "limited の時は event_id を指定してください。/limitedlist で確認できます。",
                ephemeral=True
            )
            return

        gacha_def = get_limited_gacha_by_id(event_id)
        if gacha_def is None:
            await interaction.response.send_message(
                "その限定ガチャは開催中ではありません。",
                ephemeral=True
            )
            return
    else:
        await interaction.response.send_message(
            "gacha_type は normal か limited を指定してください。",
            ephemeral=True
        )
        return

    missing = get_missing_characters_for_gacha(interaction.user.id, gacha_def)
    total = get_gacha_unique_total(gacha_def)
    owned = total - len(missing)

    text = "\n".join(missing) if missing else "コンプ済み"

    embed = discord.Embed(
        title=f"📚 {gacha_def['name']} コレクション",
        description=f"所持: {owned}/{total}",
        color=0x66CCFF
    )
    embed.add_field(name="未所持", value=text, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="top", description="HPTランキングを見る")
async def top(interaction: discord.Interaction):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    with db_lock:
        rows = conn.execute(
            "SELECT user_id, coins FROM users ORDER BY coins DESC LIMIT 10"
        ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "まだランキングデータがないよ。",
            ephemeral=True
        )
        return

    lines = []
    guild = interaction.guild
    for i, (user_id, coins) in enumerate(rows, start=1):
        member = guild.get_member(int(user_id)) if guild else None
        name = member.display_name if member else f"User {user_id}"
        lines.append(f"{i}位：{name} - {coins} HPT")

    embed = discord.Embed(
        title="🏆 HPTランキング",
        description="\n".join(lines)
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="givehpt", description="管理者用：ユーザーにHPTを送る")
@app_commands.describe(user="送り先", amount="送るHPT")
async def givehpt(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "権限がありません。",
            ephemeral=True
        )
        return

    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    if amount <= 0:
        await interaction.response.send_message(
            "1以上の数値を入力してください。",
            ephemeral=True
        )
        return

    add_coins(user.id, amount)

    await interaction.response.send_message(
        f"💸 {user.display_name} に {amount} HPT付与しました。",
        ephemeral=True
    )

if __name__ == "__main__":
    try:
        keep_alive()
        bot.run(TOKEN)
    except Exception:
        traceback.print_exc()
        raise
