import os
import re
import time
import random
import sqlite3
import threading
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from discord import app_commands
from flask import Flask

# =========================================================
# 0. CONFIG
# =========================================================
# 使い方:
# - 下の ID を新サーバーのものに置き換えてください
# - TOKEN は環境変数 TOKEN に入れてください
# - Replit / Render などで使う場合は PORT も自動対応します

JST = ZoneInfo("Asia/Tokyo")
TOKEN = os.getenv("TOKEN")

CONFIG = {
    "guild_id": 1494650547008045137,
    "db_path": "/var/data/data.db",
    "backup_dir": "/var/data/db_backups",
    "admin_role_ids": [],  # 管理者専用コマンドをこのロールにも許可したい場合に追加
    "hpt": {
        "post_reward_per_media": 25,
        "post_cooldown_seconds": 2,
        "trade_cost": 350,
        "welcome_bonus": 75,
        "reaction_reward_per_reaction": 1,
        "reaction_reward_cap_per_message": 40,
        "instant_reward": 10,
        "steal_reward": 10,
    },
    "timers": {
        "instant_delete_seconds": 15,
        "steal_window_seconds": 25,
        "action_dedup_seconds": 3.0,
    },
    "channels": {
        "welcome": 1495022420329889924,
        "gacha_log": 1495022314419654707,
        "instant_image": 1495022511811854388,
        "steal": 1495022589310140546,
        "common_command": 1495022193656991774,
        "normal_gacha": 1495022216587382896,
        # 期間限定ガチャを複数置きたい場合は、この後ろに好きなだけ追加可
        # 例: "limited_gacha_a": 0,
        #     "limited_gacha_b": 0,
    },
    # 通常の画像/動画投稿でHPTが入るチャンネル
    "reward_media_channel_ids": [
        1495022883871658025,
        1495022898870751253,
        1495022929421795479,
        1495023037962129578,
        1495023099400421376,
        1495023156358942821,
        1495023188709605517,
        1495023215049838593,
        1495023238219173971
    ],
    # リンク投稿を許可するチャンネル
    "allowed_link_channel_ids": [
        1495025081485754430,
        1495025175710928916,
    ],
    # リアクションで投稿者にHPTを付与するチャンネル
    "reaction_reward_channel_ids": [
        1495023449096327238,
    ],
}

# =========================================================
# 1. CHARACTER MASTER
# =========================================================
# 復刻や再利用を楽にするため、キャラ情報はここだけで管理
CHARACTERS = {
    "char_001": {
        "name": "[001]みゆ",
        "rarity": "S",
        "image_url": "https://example.com/char_001.png",
    },
    "char_002": {
        "name": "[002]りみ",
        "rarity": "S",
        "image_url": "https://example.com/char_002.png",
    },
    "char_003": {
        "name": "[003]さえ",
        "rarity": "A",
        "image_url": "https://example.com/char_003.png",
    },
    # 必要なキャラを追加
}

# =========================================================
# 2. GACHA MASTER
# =========================================================
# items は (character_id, weight)
# /gacha は「実行されたチャンネルID」と「現在時刻」で自動的に対象ガチャを判定します
GACHAS = [
    {
        "id": "normal_2026_04",
        "name": "通常ガチャ 2026年4月",
        "kind": "normal",
        "channel_id": CONFIG["channels"]["normal_gacha"],
        "start": "2026-04-01 00:00",
        "end": "2026-04-30 23:59",
        "cost": 50,
        "role_id": 1495023507451547730,
        "items": [
            ("char_001", 8),
            ("char_002", 8),
            ("char_003", 12),
        ],
    },
    # 例: 期間限定を追加する場合
    # {
    #     "id": "limited_serina_revival_2026_05",
    #     "name": "せりな復刻ガチャ",
    #     "kind": "limited",
    #     "channel_id": CONFIG["channels"]["limited_gacha_a"],
    #     "start": "2026-05-01 00:00",
    #     "end": "2026-05-07 23:59",
    #     "cost": 75,
    #     "role_id": 0,
    #     "items": [
    #         ("char_001", 10),
    #         ("char_002", 10),
    #     ],
    # },
]

LINK_PATTERNS = [
    r"https?://",
    r"www\\.",
    r"discord\\.gg/",
    r"discord\\.com/invite/",
]

MEDIA_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".mp4", ".mov", ".webm", ".mkv",
)

IMAGE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
)

# =========================================================
# 3. KEEP ALIVE (Flask)
# =========================================================
app = Flask("")


@app.route("/")
def home():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


def keep_alive():
    t = threading.Thread(target=run_web, daemon=True)
    t.start()


# =========================================================
# 4. DISCORD BOT
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = CONFIG["db_path"]
BACKUP_DIR = CONFIG["backup_dir"]

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.Lock()
recent_action_lock = threading.Lock()
recent_actions = {}
instant_posts_lock = threading.Lock()
latest_instant_post = None

with db_lock:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            coins INTEGER DEFAULT 0,
            last_post REAL DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gacha_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            gacha_id TEXT NOT NULL,
            gacha_name TEXT NOT NULL,
            gacha_kind TEXT NOT NULL,
            character_id TEXT NOT NULL,
            character_name TEXT NOT NULL,
            rarity TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS completion_rewards (
            gacha_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            awarded_at REAL NOT NULL,
            PRIMARY KEY (gacha_id, user_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_characters (
            user_id TEXT NOT NULL,
            character_id TEXT NOT NULL,
            obtained_at REAL NOT NULL,
            PRIMARY KEY (user_id, character_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_rewards (
            user_id TEXT PRIMARY KEY,
            awarded_at REAL NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reaction_rewards (
            message_id TEXT NOT NULL,
            reactor_user_id TEXT NOT NULL,
            author_user_id TEXT NOT NULL,
            awarded_at REAL NOT NULL,
            PRIMARY KEY (message_id, reactor_user_id)
        )
        """
    )

    conn.commit()


# =========================================================
# 5. HELPERS
# =========================================================
def now_jst() -> datetime:
    return datetime.now(JST)


def parse_jst(dt_str: str) -> datetime:
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=JST)


def backup_database():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = now_jst().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"data_backup_{ts}.db")

        with sqlite3.connect(backup_path) as backup_conn:
            with db_lock:
                conn.backup(backup_conn)

        backups = sorted(
            os.path.join(BACKUP_DIR, f)
            for f in os.listdir(BACKUP_DIR)
            if f.endswith(".db")
        )
        while len(backups) > 10:
            old = backups.pop(0)
            try:
                os.remove(old)
            except OSError:
                pass

        print(f"DB backup created: {backup_path}")
    except Exception as e:
        print(f"DB backup error: {e}")


def get_channel_id(key: str) -> int:
    return int(CONFIG["channels"].get(key, 0) or 0)


def get_guild_id() -> int:
    return int(CONFIG["guild_id"])


def is_gacha_active(gacha_def: dict) -> bool:
    now = now_jst()
    return parse_jst(gacha_def["start"]) <= now < parse_jst(gacha_def["end"])


def get_active_gacha_for_channel(channel_id: int):
    matches = [g for g in GACHAS if g["channel_id"] == channel_id and is_gacha_active(g)]
    if not matches:
        return None
    # 同一チャンネルに複数の開催中ガチャを置かない前提
    return matches[0]


def get_gacha_by_id(gacha_id: str):
    for g in GACHAS:
        if g["id"] == gacha_id:
            return g
    return None


def get_character(character_id: str):
    return CHARACTERS.get(character_id)


def get_character_name(character_id: str) -> str:
    data = get_character(character_id)
    return data["name"] if data else character_id


def get_character_rarity(character_id: str) -> str:
    data = get_character(character_id)
    return data["rarity"] if data else "?"


def get_character_image(character_id: str) -> str:
    data = get_character(character_id)
    return data.get("image_url", "") if data else ""


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
    return content_type.startswith("image/") or filename.endswith(IMAGE_EXTENSIONS)


def count_media_attachments(message: discord.Message) -> int:
    count = 0
    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        filename = attachment.filename.lower()
        if content_type.startswith("image/") or content_type.startswith("video/"):
            count += 1
        elif filename.endswith(MEDIA_EXTENSIONS):
            count += 1
    return count


def check_and_mark_recent_action(user_id: int, action_name: str) -> bool:
    now_ts = time.time()
    dedup_seconds = float(CONFIG["timers"]["action_dedup_seconds"])
    key = (user_id, action_name)

    with recent_action_lock:
        expired = [k for k, ts in recent_actions.items() if now_ts - ts > dedup_seconds]
        for k in expired:
            recent_actions.pop(k, None)

        last_ts = recent_actions.get(key)
        if last_ts is not None and now_ts - last_ts < dedup_seconds:
            return False

        recent_actions[key] = now_ts
        return True


def get_user(user_id: int):
    with db_lock:
        row = conn.execute(
            "SELECT coins, last_post FROM users WHERE user_id=?",
            (str(user_id),)
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO users (user_id, coins, last_post) VALUES (?, 0, 0)",
                (str(user_id),)
            )
            conn.commit()
            return 0, 0

        return int(row[0]), float(row[1])


def add_coins(user_id: int, amount: int):
    current, last_post = get_user(user_id)
    new_amount = max(0, current + amount)
    with db_lock:
        conn.execute(
            "UPDATE users SET coins=?, last_post=? WHERE user_id=?",
            (new_amount, last_post, str(user_id))
        )
        conn.commit()
    return new_amount


def set_last_post(user_id: int):
    with db_lock:
        conn.execute(
            "UPDATE users SET last_post=? WHERE user_id=?",
            (time.time(), str(user_id))
        )
        conn.commit()


def log_gacha(user_id: int, gacha_def: dict, character_id: str):
    with db_lock:
        conn.execute(
            """
            INSERT INTO gacha_logs (
                user_id, gacha_id, gacha_name, gacha_kind,
                character_id, character_name, rarity, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(user_id),
                gacha_def["id"],
                gacha_def["name"],
                gacha_def["kind"],
                character_id,
                get_character_name(character_id),
                get_character_rarity(character_id),
                time.time(),
            )
        )
        conn.commit()


def add_user_character(user_id: int, character_id: str):
    with db_lock:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_characters (user_id, character_id, obtained_at)
            VALUES (?, ?, ?)
            """,
            (str(user_id), character_id, time.time())
        )
        conn.commit()


def user_has_character_global(user_id: int, character_id: str) -> bool:
    with db_lock:
        row = conn.execute(
            "SELECT 1 FROM user_characters WHERE user_id=? AND character_id=? LIMIT 1",
            (str(user_id), character_id)
        ).fetchone()
    return row is not None


def get_user_owned_character_ids(user_id: int):
    with db_lock:
        rows = conn.execute(
            "SELECT character_id FROM user_characters WHERE user_id=? ORDER BY obtained_at ASC",
            (str(user_id),)
        ).fetchall()
    return [row[0] for row in rows]


def get_gacha_unique_total(gacha_def: dict) -> int:
    return len({character_id for character_id, _weight in gacha_def["items"]})


def get_user_unique_count_for_gacha(user_id: int, gacha_def: dict) -> int:
    owned_ids = set(get_user_owned_character_ids(user_id))
    gacha_ids = {character_id for character_id, _weight in gacha_def["items"]}
    return len(owned_ids & gacha_ids)


def get_missing_ids_for_gacha(user_id: int, gacha_def: dict):
    owned_ids = set(get_user_owned_character_ids(user_id))
    return [character_id for character_id, _ in gacha_def["items"] if character_id not in owned_ids]


def get_missing_names_for_gacha(user_id: int, gacha_def: dict):
    return [get_character_name(cid) for cid in get_missing_ids_for_gacha(user_id, gacha_def)]


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


def has_welcome_reward(user_id: int) -> bool:
    with db_lock:
        row = conn.execute(
            "SELECT 1 FROM welcome_rewards WHERE user_id=?",
            (str(user_id),)
        ).fetchone()
    return row is not None


def mark_welcome_reward(user_id: int):
    with db_lock:
        conn.execute(
            "INSERT OR IGNORE INTO welcome_rewards (user_id, awarded_at) VALUES (?, ?)",
            (str(user_id), time.time())
        )
        conn.commit()


def get_reaction_reward_count_for_message(message_id: int) -> int:
    with db_lock:
        row = conn.execute(
            "SELECT COUNT(*) FROM reaction_rewards WHERE message_id=?",
            (str(message_id),)
        ).fetchone()
    return int(row[0]) if row else 0


def has_reaction_reward_record(message_id: int, reactor_user_id: int) -> bool:
    with db_lock:
        row = conn.execute(
            "SELECT 1 FROM reaction_rewards WHERE message_id=? AND reactor_user_id=?",
            (str(message_id), str(reactor_user_id))
        ).fetchone()
    return row is not None


def add_reaction_reward_record(message_id: int, reactor_user_id: int, author_user_id: int) -> bool:
    with db_lock:
        try:
            conn.execute(
                """
                INSERT INTO reaction_rewards (message_id, reactor_user_id, author_user_id, awarded_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(message_id), str(reactor_user_id), str(author_user_id), time.time())
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def roll_from_items(items):
    total = sum(weight for _character_id, weight in items)
    r = random.randint(1, total)
    current = 0
    for character_id, weight in items:
        current += weight
        if r <= current:
            return character_id
    return items[-1][0]


def is_admin_member(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    admin_role_ids = set(int(x) for x in CONFIG.get("admin_role_ids", []))
    if not admin_role_ids:
        return False
    return any(role.id in admin_role_ids for role in member.roles)


# =========================================================
# 6. UI
# =========================================================
class CharacterSelect(discord.ui.Select):
    def __init__(self, owner_id: int, owned_ids: list[str]):
        self.owner_id = owner_id
        options = []
        for cid in owned_ids[:25]:
            char = get_character(cid)
            if not char:
                continue
            options.append(
                discord.SelectOption(
                    label=char["name"][:100],
                    description=f"レアリティ: {char['rarity']}",
                    value=cid,
                )
            )
        super().__init__(
            placeholder="カードを表示したいキャラを選択",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="キャラなし", value="none")],
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("このメニューはコマンド実行者専用です。", ephemeral=True)
            return

        cid = self.values[0]
        if cid == "none":
            await interaction.response.send_message("表示できるキャラがありません。", ephemeral=True)
            return

        char = get_character(cid)
        if not char:
            await interaction.response.send_message("キャラ情報が見つかりませんでした。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🃏 {char['name']}",
            description=f"レアリティ: **{char['rarity']}**\nID: `{cid}`",
            color=0xFFD700,
        )
        image_url = char.get("image_url")
        if image_url:
            embed.set_image(url=image_url)

        await interaction.response.send_message(embed=embed, ephemeral=True)


class CharacterSelectView(discord.ui.View):
    def __init__(self, owner_id: int, owned_ids: list[str]):
        super().__init__(timeout=120)
        self.add_item(CharacterSelect(owner_id, owned_ids))


# =========================================================
# 7. LOG / ROLE HELPERS
# =========================================================
async def send_gacha_log(user: discord.abc.User, gacha_def: dict, character_id: str, remaining_count: int, action: str):
    channel = bot.get_channel(get_channel_id("gacha_log"))
    if channel is None:
        return

    now_text = now_jst().strftime("%m/%d %H:%M")
    remain_text = "コンプ" if remaining_count <= 0 else f"残{remaining_count}"

    try:
        await channel.send(
            f"`{now_text}` {user.display_name}｜{gacha_def['name']}｜{get_character_name(character_id)}｜{action}｜{remain_text}"
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def award_completion_role_if_needed(member: discord.Member, gacha_def: dict):
    role_id = int(gacha_def.get("role_id", 0) or 0)
    if not role_id:
        return None

    total_needed = get_gacha_unique_total(gacha_def)
    owned_count = get_user_unique_count_for_gacha(member.id, gacha_def)
    if owned_count < total_needed:
        return None

    role = member.guild.get_role(role_id)
    if role is None:
        return None

    if role not in member.roles:
        try:
            await member.add_roles(role, reason=f"{gacha_def['name']} コンプ報酬")
        except (discord.Forbidden, discord.HTTPException):
            return None

    if not has_completion_reward_record(gacha_def["id"], member.id):
        add_completion_reward_record(gacha_def["id"], member.id)
        return role

    return None


async def remove_expired_completion_roles():
    active_ids = {g["id"] for g in GACHAS if is_gacha_active(g)}
    gacha_map = {g["id"]: g for g in GACHAS}

    with db_lock:
        rows = conn.execute("SELECT gacha_id, user_id FROM completion_rewards").fetchall()

    for gacha_id, user_id_str in rows:
        if gacha_id in active_ids:
            continue

        gacha_def = gacha_map.get(gacha_id)
        user_id = int(user_id_str)

        if not gacha_def:
            remove_completion_reward_record(gacha_id, user_id)
            continue

        role_id = int(gacha_def.get("role_id", 0) or 0)
        if not role_id:
            remove_completion_reward_record(gacha_id, user_id)
            continue

        guild = bot.get_guild(get_guild_id())
        if guild:
            member = guild.get_member(user_id)
            role = guild.get_role(role_id)
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=f"{gacha_def['name']} 期間終了")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        remove_completion_reward_record(gacha_id, user_id)


# =========================================================
# 8. TASKS
# =========================================================
@tasks.loop(minutes=25)
async def periodic_cleanup():
    await remove_expired_completion_roles()


# =========================================================
# 9. SPECIAL CHANNELS
# =========================================================
async def handle_instant_channel(message: discord.Message):
    global latest_instant_post

    attachments = message.attachments
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

    add_coins(message.author.id, int(CONFIG["hpt"]["instant_reward"]))

    with instant_posts_lock:
        latest_instant_post = {
            "message_id": message.id,
            "author_id": message.author.id,
            "created_at": time.time(),
            "stolen": False,
        }

    try:
        await message.delete(delay=float(CONFIG["timers"]["instant_delete_seconds"]))
    except discord.HTTPException:
        pass


async def handle_steal_channel(message: discord.Message):
    global latest_instant_post

    if not message.content.strip():
        return

    has_image = any(is_image_attachment(att) for att in message.attachments)
    if not has_image:
        return

    now_ts = time.time()
    with instant_posts_lock:
        data = latest_instant_post
        if data is None:
            return
        if now_ts - data["created_at"] > float(CONFIG["timers"]["steal_window_seconds"]):
            return
        if data["stolen"]:
            return
        if data["author_id"] == message.author.id:
            return
        data["stolen"] = True

    add_coins(message.author.id, int(CONFIG["hpt"]["steal_reward"]))

    try:
        await message.add_reaction("💰")
    except Exception:
        pass


# =========================================================
# 10. AUTOCOMPLETE
# =========================================================
async def trade_character_autocomplete(interaction: discord.Interaction, current: str):
    gacha_def = get_active_gacha_for_channel(interaction.channel_id)
    if gacha_def is None:
        return []

    choices = []
    for character_id, _weight in gacha_def["items"]:
        if user_has_character_global(interaction.user.id, character_id):
            continue
        name = get_character_name(character_id)
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=name, value=character_id))
    return choices[:25]


# =========================================================
# 11. EVENTS
# =========================================================
@bot.event
async def on_ready():
    print("TOKEN exists:", bool(TOKEN))
    backup_database()

    try:
        guild_obj = discord.Object(id=get_guild_id())
        bot.tree.copy_global_to(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
        print(f"Guild synced: {len(synced)} commands")
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
async def on_member_join(member: discord.Member):
    try:
        if member.bot:
            return

        get_user(member.id)
        if has_welcome_reward(member.id):
            return

        bonus = int(CONFIG["hpt"]["welcome_bonus"])
        add_coins(member.id, bonus)
        mark_welcome_reward(member.id)

        welcome_channel = bot.get_channel(get_channel_id("welcome"))
        if welcome_channel:
            embed = discord.Embed(
                title="🎉 ようこそ！",
                description=(
                    f"{member.mention} さん、参加ありがとうございます！\n"
                    f"ウェルカムボーナスとして **{bonus}HPT** を付与しました。\n\n"
                    "このサーバーでは、画像・動画投稿やガチャで遊べます。\n"
                    "まずは残高確認chより `/balance` でHPTを確認してみてください。"
                ),
                color=0x66CCFF,
            )
            await welcome_channel.send(embed=embed)
    except Exception:
        traceback.print_exc()


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    try:
        if payload.guild_id != get_guild_id():
            return
        if payload.user_id == bot.user.id:
            return
        if payload.channel_id not in set(int(x) for x in CONFIG["reaction_reward_channel_ids"]):
            return

        guild = bot.get_guild(payload.guild_id)
        if guild is None:
            return

        reactor = guild.get_member(payload.user_id)
        if reactor is None or reactor.bot:
            return

        channel = bot.get_channel(payload.channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        if message.author.bot:
            return
        if message.author.id == payload.user_id:
            return

        cap = int(CONFIG["hpt"]["reaction_reward_cap_per_message"])
        if get_reaction_reward_count_for_message(message.id) >= cap:
            return

        if has_reaction_reward_record(message.id, payload.user_id):
            return

        inserted = add_reaction_reward_record(message.id, payload.user_id, message.author.id)
        if not inserted:
            return

        add_coins(message.author.id, int(CONFIG["hpt"]["reaction_reward_per_reaction"]))
    except Exception:
        traceback.print_exc()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # リンク制限
    if message.channel.id not in set(int(x) for x in CONFIG["allowed_link_channel_ids"]):
        if has_disallowed_link(message.content):
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

    # 一瞬投稿チャンネル
    if message.channel.id == get_channel_id("instant_image"):
        await handle_instant_channel(message)
        await bot.process_commands(message)
        return

    # steal チャンネル
    if message.channel.id == get_channel_id("steal"):
        await handle_steal_channel(message)
        await bot.process_commands(message)
        return

    # 通常の投稿報酬
    if message.channel.id in set(int(x) for x in CONFIG["reward_media_channel_ids"]):
        media_count = count_media_attachments(message)
        if media_count > 0:
            coins, last_post = get_user(message.author.id)
            cooldown = float(CONFIG["hpt"]["post_cooldown_seconds"])
            if time.time() - last_post > cooldown:
                reward = media_count * int(CONFIG["hpt"]["post_reward_per_media"])
                add_coins(message.author.id, reward)
                set_last_post(message.author.id)

    await bot.process_commands(message)


# =========================================================
# 12. COMMANDS
# =========================================================
@bot.tree.command(name="balance", description="自分のHPTを見る")
async def balance(interaction: discord.Interaction):
    if interaction.channel_id != get_channel_id("common_command"):
        await interaction.response.send_message(
            "このコマンドは共通コマンドチャンネルで使ってください。",
            ephemeral=True,
        )
        return

    coins, _ = get_user(interaction.user.id)
    await interaction.response.send_message(
        f"💰 {interaction.user.display_name} のHPT: {coins}",
        ephemeral=True,
    )


@bot.tree.command(name="top", description="HPTランキングを見る")
async def top(interaction: discord.Interaction):
    if interaction.channel_id != get_channel_id("common_command"):
        await interaction.response.send_message(
            "このコマンドは共通コマンドチャンネルで使ってください。",
            ephemeral=True,
        )
        return

    with db_lock:
        rows = conn.execute(
            "SELECT user_id, coins FROM users ORDER BY coins DESC LIMIT 10"
        ).fetchall()

    if not rows:
        await interaction.response.send_message("まだランキングデータがありません。", ephemeral=True)
        return

    lines = []
    guild = interaction.guild
    for i, (user_id_str, coins) in enumerate(rows, start=1):
        member = guild.get_member(int(user_id_str)) if guild else None
        name = member.display_name if member else f"User {user_id_str}"
        lines.append(f"{i}位：{name} - {coins}HPT")

    embed = discord.Embed(
        title="🏆 HPTランキング",
        description="\n".join(lines),
        color=0xFFD700,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="givehpt", description="指定ユーザーにHPTを付与する（管理者用）")
@app_commands.describe(member="付与先", amount="付与するHPT")
async def givehpt(interaction: discord.Interaction, member: discord.Member, amount: int):
    if interaction.channel_id != get_channel_id("common_command"):
        await interaction.response.send_message(
            "このコマンドは共通コマンドチャンネルで使ってください。",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.user, discord.Member) or not is_admin_member(interaction.user):
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    if amount <= 0:
        await interaction.response.send_message("付与するHPTは1以上で指定してください。", ephemeral=True)
        return

    new_amount = add_coins(member.id, amount)
    await interaction.response.send_message(
        f"✅ {member.display_name} に {amount}HPT を付与しました。現在: {new_amount}HPT",
        ephemeral=True,
    )


@bot.tree.command(name="gacha", description="このチャンネルに対応したガチャを引く")
async def gacha(interaction: discord.Interaction):
    gacha_def = get_active_gacha_for_channel(interaction.channel_id)
    if gacha_def is None:
        await interaction.response.send_message(
            "このチャンネルで開催中のガチャはありません。",
            ephemeral=True,
        )
        return

    if not check_and_mark_recent_action(interaction.user.id, "gacha"):
        await interaction.response.send_message(
            "処理中です。少し待ってからもう一度試してください。",
            ephemeral=True,
        )
        return

    coins, _ = get_user(interaction.user.id)
    cost = int(gacha_def.get("cost", 50))
    if coins < cost:
        await interaction.response.send_message(
            f"HPTが足りません。現在 {coins}HPT、必要 {cost}HPT です。",
            ephemeral=True,
        )
        return

    add_coins(interaction.user.id, -cost)
    character_id = roll_from_items(gacha_def["items"])
    add_user_character(interaction.user.id, character_id)
    log_gacha(interaction.user.id, gacha_def, character_id)

    missing = get_missing_names_for_gacha(interaction.user.id, gacha_def)
    complete_role = None
    if isinstance(interaction.user, discord.Member):
        complete_role = await award_completion_role_if_needed(interaction.user, gacha_def)

    await send_gacha_log(interaction.user, gacha_def, character_id, len(missing), "ガチャ")

    embed = discord.Embed(
        title=f"🎰 {gacha_def['name']} 結果",
        description=f"{get_character_rarity(character_id)}\n**{get_character_name(character_id)}**",
        color=0xFFD700 if gacha_def["kind"] == "normal" else 0xFF66CC,
    )
    image_url = get_character_image(character_id)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=f"{cost}HPT消費しました")

    if complete_role:
        embed.add_field(
            name="🎉 コンプ達成",
            value=f"{gacha_def['name']} をコンプしました。\nロール「{complete_role.name}」を付与しました。",
            inline=False,
        )
    else:
        embed.add_field(
            name="📘 コンプ状況",
            value="コンプ済み" if not missing else f"残り {len(missing)}種",
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="trade_normal", description="開催中ガチャの未所持キャラを交換する")
@app_commands.describe(character="交換したいキャラ")
@app_commands.autocomplete(character=trade_character_autocomplete)
async def trade_normal(interaction: discord.Interaction, character: str):
    gacha_def = get_active_gacha_for_channel(interaction.channel_id)
    if gacha_def is None:
        await interaction.response.send_message(
            "このチャンネルで開催中のガチャはありません。",
            ephemeral=True,
        )
        return

    if not check_and_mark_recent_action(interaction.user.id, "trade_normal"):
        await interaction.response.send_message(
            "処理中です。少し待ってからもう一度試してください。",
            ephemeral=True,
        )
        return

    item_ids = {character_id for character_id, _weight in gacha_def["items"]}
    if character not in item_ids:
        await interaction.response.send_message(
            "そのキャラはこのガチャには含まれていません。",
            ephemeral=True,
        )
        return

    if user_has_character_global(interaction.user.id, character):
        await interaction.response.send_message(
            "そのキャラはすでに所持しています。未所持キャラのみ交換できます。",
            ephemeral=True,
        )
        return

    trade_cost = int(CONFIG["hpt"]["trade_cost"])
    coins, _ = get_user(interaction.user.id)
    if coins < trade_cost:
        await interaction.response.send_message(
            f"HPTが足りません。現在 {coins}HPT、必要 {trade_cost}HPT です。",
            ephemeral=True,
        )
        return

    add_coins(interaction.user.id, -trade_cost)
    add_user_character(interaction.user.id, character)
    log_gacha(interaction.user.id, gacha_def, character)

    missing = get_missing_names_for_gacha(interaction.user.id, gacha_def)
    complete_role = None
    if isinstance(interaction.user, discord.Member):
        complete_role = await award_completion_role_if_needed(interaction.user, gacha_def)

    await send_gacha_log(interaction.user, gacha_def, character, len(missing), "交換")

    embed = discord.Embed(
        title=f"🛒 {gacha_def['name']} 交換結果",
        description=f"**{get_character_name(character)}** を交換しました",
        color=0x66FF99,
    )
    image_url = get_character_image(character)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=f"{trade_cost}HPT消費しました")

    if complete_role:
        embed.add_field(
            name="🎉 コンプ達成",
            value=f"{gacha_def['name']} をコンプしました。\nロール「{complete_role.name}」を付与しました。",
            inline=False,
        )
    else:
        embed.add_field(
            name="📘 コンプ状況",
            value="コンプ済み" if not missing else f"残り {len(missing)}種",
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="mycharacters", description="自分の所持キャラ一覧を見る")
async def mycharacters(interaction: discord.Interaction):
    if interaction.channel_id != get_channel_id("common_command"):
        await interaction.response.send_message(
            "このコマンドは共通コマンドチャンネルで使ってください。",
            ephemeral=True,
        )
        return

    owned_ids = get_user_owned_character_ids(interaction.user.id)
    if not owned_ids:
        await interaction.response.send_message("まだ所持キャラがありません。", ephemeral=True)
        return

    names = [get_character_name(cid) for cid in owned_ids]
    chunks = []
    current = ""
    for name in names:
        line = f"{name}\n"
        if len(current) + len(line) > 1000:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    embed = discord.Embed(
        title=f"🗂 {interaction.user.display_name} の所持キャラ",
        description=f"所持数: {len(names)}\n下のメニューから選ぶとカード表示できます。",
        color=0x66CCFF,
    )
    for i, chunk in enumerate(chunks[:5], start=1):
        embed.add_field(name=f"一覧{i}", value=chunk, inline=False)

    view = CharacterSelectView(interaction.user.id, owned_ids)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# =========================================================
# 13. MAIN
# =========================================================
def validate_config():
    if not TOKEN:
        raise RuntimeError("環境変数 TOKEN が設定されていません。")
    if not CONFIG["guild_id"]:
        raise RuntimeError("CONFIG['guild_id'] を設定してください。")
    if get_channel_id("common_command") == 0:
        print("[WARN] common_command チャンネルIDが未設定です。")

    seen = set()
    for g in GACHAS:
        gid = g["id"]
        if gid in seen:
            raise RuntimeError(f"ガチャIDが重複しています: {gid}")
        seen.add(gid)
        for cid, weight in g["items"]:
            if cid not in CHARACTERS:
                raise RuntimeError(f"ガチャ {gid} に未定義キャラ {cid} があります。")
            if weight <= 0:
                raise RuntimeError(f"ガチャ {gid} の weight が不正です: {cid} -> {weight}")


def main():
    validate_config()
    keep_alive()
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
