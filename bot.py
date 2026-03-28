from flask import Flask
import threading
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import random
import time
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

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
REPORT_HEADER = "🎰 ガチャ総合結果"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = "/var/data/data.db"
conn = sqlite3.connect("/var/data/data.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    coins INTEGER DEFAULT 0,
    last_post REAL DEFAULT 0
)
""")

cur.execute("""
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

cur.execute("""
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS completion_rewards (
    gacha_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    awarded_at REAL NOT NULL,
    PRIMARY KEY (gacha_id, user_id)
)
""")

conn.commit()

def get_user(user_id: int):
    cur.execute("SELECT coins, last_post FROM users WHERE user_id=?", (str(user_id),))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (user_id, coins, last_post) VALUES (?, 0, 0)",
            (str(user_id),)
        )
        conn.commit()
        return 0, 0
    return row

def add_coins(user_id: int, amount: int):
    coins, _ = get_user(user_id)
    new_amount = max(0, coins + amount)
    cur.execute("UPDATE users SET coins=? WHERE user_id=?", (new_amount, str(user_id)))
    conn.commit()

def set_last_post(user_id: int):
    cur.execute("UPDATE users SET last_post=? WHERE user_id=?", (time.time(), str(user_id)))
    conn.commit()

def log_gacha(user_id: int, gacha_id: str, gacha_name: str, gacha_type: str, character_name: str, rarity: str):
    cur.execute(
        """
        INSERT INTO gacha_logs (user_id, gacha_id, gacha_name, gacha_type, character_name, rarity, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(user_id), gacha_id, gacha_name, gacha_type, character_name, rarity, time.time())
    )
    conn.commit()

def get_setting(key: str):
    cur.execute("SELECT value FROM bot_settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else None

def set_setting(key: str, value: str):
    cur.execute("""
        INSERT INTO bot_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()

def add_completion_reward_record(gacha_id: str, user_id: int):
    cur.execute("""
        INSERT INTO completion_rewards (gacha_id, user_id, awarded_at)
        VALUES (?, ?, ?)
        ON CONFLICT(gacha_id, user_id) DO NOTHING
    """, (gacha_id, str(user_id), time.time()))
    conn.commit()

def has_completion_reward_record(gacha_id: str, user_id: int) -> bool:
    cur.execute(
        "SELECT 1 FROM completion_rewards WHERE gacha_id=? AND user_id=?",
        (gacha_id, str(user_id))
    )
    return cur.fetchone() is not None

def remove_completion_reward_record(gacha_id: str, user_id: int):
    cur.execute(
        "DELETE FROM completion_rewards WHERE gacha_id=? AND user_id=?",
        (gacha_id, str(user_id))
    )
    conn.commit()

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
REPORT_CHANNEL_ID = 1487008334358773842

# =========================================
# ここを編集して使う
# =========================================

# 通常ガチャ（週替わり）
# start/end は JST で判定
# role_id はその週のコンプロール
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
            ("[001]みゆ", "S", 9, "https://cdn.discordapp.com/attachments/1486776583858425911/1486834490017055010/S.png?ex=69c6f206&is=69c5a086&hm=69ea5c80115bc07d31794aeefad633b5a099eb68336ce3fa79ff63ba8ac83f22&"),
            ("[002]りみ", "S", 9, "https://cdn.discordapp.com/attachments/1486776583858425911/1486840365032935446/S_1.png?ex=69c6f77f&is=69c5a5ff&hm=260cee99ea95db450ce7dc8a71308bb0ae6bc04a0eff5412cae66247c91b5d7d&"),
            ("[003]さえ", "S", 9, "https://cdn.discordapp.com/attachments/1486863251525603478/1486871101710663770/S_3.png?ex=69c7141f&is=69c5c29f&hm=86c5c46921441db38ca19811c6e693c90fb0227b2046e85f84467e97553c0d2a&"),
            ("[004]ふうあ", "S", 9, "https://cdn.discordapp.com/attachments/1486863251525603478/1486876158749315155/S_2.png?ex=69c718d5&is=69c5c755&hm=4c361ef76e57c20fc3d8bc99484613366ee32455ecaf2184e6df3c0f36062542&"),
            ("[005]そら", "S", 9, "https://cdn.discordapp.com/attachments/1487010239650988182/1487012914228494365/S_3.png?ex=69c79832&is=69c646b2&hm=a6be733f8bbed7cb41feff01e5e2774b34b6b53d39e7a929db134cc030a65740&"),
            ("[006]せりな","S",9,"https://cdn.discordapp.com/attachments/1486776583858425911/1487110091193843783/S_4.png?ex=69c7f2b2&is=69c6a132&hm=7fce72b32da9c4c844d180a57f25ca616c9f06ee412ccee39955cb45e09a7973&"),
            ("[007]せな","S", 9,"https://cdn.discordapp.com/attachments/1487059067254870078/1487112005620858900/S_5.png?ex=69c7f47b&is=69c6a2fb&hm=0f71015b6d6386cf0a402aa910c3849045672e525133428a9736347b6f1145f2&"),
            ("[008]ゆうな","S", 9,"https://cdn.discordapp.com/attachments/1487131651677880480/1487142082178191500/S_6.png?ex=69c8107e&is=69c6befe&hm=d2bc038c8ba07020cb47dcb1ad8e2cf2599a0ee4f55dfe370fb71b10bfc4e9ed&"),
            ("[009]ここな","B", 22,"https://cdn.discordapp.com/attachments/1486776583858425911/1487150046339141704/image.png?ex=69c817e8&is=69c6c668&hm=b8eb1f263fa314d015aa16ce7fffda080c484790748fd67eed12f448ba9f381f&"),
            ("[010]みう","A",15,"https://cdn.discordapp.com/attachments/1486776583858425911/1487150273628733522/image.png?ex=69c8181f&is=69c6c69f&hm=3cbbafe3e43be3970fb7cde3f10d4670acb7eb8c699396cf069ba789e00417ab&")
        ]
    }
]

# 限定ガチャ
# 後から追加しやすいようにここに足すだけ
LIMITED_GACHAS = [
    # 例
    # {
    #     "id": "limited_2026_march_01",
    #     "name": "3月限定ガチャ",
    #     "type": "limited",
    #     "start": "2026-03-01 00:00",
    #     "end": "2026-03-31 23:59",
    #     "role_id": 0,  # ←ここを限定コンプロールIDに変更
    #     "cost": 50,
    #     "items": [
    #         ("限定A", "S", 50, "https://example.com/a.png"),
    #         ("限定B", "S", 50, "https://example.com/b.png")
    #     ]
    # }
]

# =========================================
# ここから下は基本そのまま
# =========================================

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
    cur.execute("""
        SELECT COUNT(DISTINCT character_name)
        FROM gacha_logs
        WHERE user_id=? AND gacha_id=?
    """, (str(user_id), gacha_id))
    row = cur.fetchone()
    return row[0] if row and row[0] else 0

def get_missing_characters_for_gacha(user_id: int, gacha_def: dict):
    owned = set()
    cur.execute("""
        SELECT DISTINCT character_name
        FROM gacha_logs
        WHERE user_id=? AND gacha_id=?
    """, (str(user_id), gacha_def["id"]))
    for row in cur.fetchall():
        owned.add(row[0])

    all_chars = [item[0] for item in gacha_def["items"]]
    return [name for name in all_chars if name not in owned]

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

    cur.execute("SELECT gacha_id, user_id FROM completion_rewards")
    reward_rows = cur.fetchall()

    gacha_map = {g["id"]: g for g in all_gachas}

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

def build_report_text():
    cur.execute("""
        SELECT COUNT(DISTINCT user_id)
        FROM gacha_logs
    """)
    user_count = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT COUNT(*)
        FROM gacha_logs
    """)
    total_count = cur.fetchone()[0] or 0

    lines = [
        REPORT_HEADER,
        f"総ガチャ回数: {total_count}回",
        f"ガチャを引いた人数: {user_count}人",
        ""
    ]

    weekly = get_active_weekly_gacha()
    if weekly:
        lines.append(f"【通常】{weekly['name']}")
        cur.execute("""
            SELECT character_name, COUNT(*)
            FROM gacha_logs
            WHERE gacha_id=?
            GROUP BY character_name
            ORDER BY COUNT(*) DESC, character_name ASC
        """, (weekly["id"],))
        rows = cur.fetchall()
        if not rows:
            lines.append("まだデータなし")
        else:
            for name, count in rows:
                lines.append(f"{name}: {count}回")
        lines.append("")

    limited_list = get_active_limited_gachas()
    for g in limited_list:
        lines.append(f"【限定】{g['name']}")
        cur.execute("""
            SELECT character_name, COUNT(*)
            FROM gacha_logs
            WHERE gacha_id=?
            GROUP BY character_name
            ORDER BY COUNT(*) DESC, character_name ASC
        """, (g["id"],))
        rows = cur.fetchall()
        if not rows:
            lines.append("まだデータなし")
        else:
            for name, count in rows:
                lines.append(f"{name}: {count}回")
        lines.append("")

    if not weekly and not limited_list:
        lines.append("開催中のガチャはありません。")
        lines.append("")

    lines.append("このメッセージは1時間ごとに自動更新されます。")
    return "\n".join(lines)

async def update_report_message():
    channel = bot.get_channel(REPORT_CHANNEL_ID)
    if channel is None:
        return

    text = build_report_text()
    saved_message_id = get_setting("report_message_id")

    if saved_message_id:
        try:
            message = await channel.fetch_message(int(saved_message_id))
            await message.edit(content=text)
            return
        except discord.NotFound:
            pass
        except discord.Forbidden:
            return
        except discord.HTTPException:
            return

    # 保存済みIDが無い時、既存の同じ集計メッセージを探して再利用
    found_messages = []
    try:
        async for msg in channel.history(limit=30):
            if msg.author.id == bot.user.id and msg.content.startswith(REPORT_HEADER):
                found_messages.append(msg)
    except discord.Forbidden:
        return
    except discord.HTTPException:
        return

    if found_messages:
        # 最新1件を使って、残りは削除
        found_messages.sort(key=lambda m: m.created_at, reverse=True)
        main_msg = found_messages[0]
        try:
            await main_msg.edit(content=text)
            set_setting("report_message_id", str(main_msg.id))
        except discord.HTTPException:
            return

        for extra_msg in found_messages[1:]:
            try:
                await extra_msg.delete()
            except discord.HTTPException:
                pass
        return

    try:
        new_message = await channel.send(text)
        set_setting("report_message_id", str(new_message.id))
    except discord.Forbidden:
        return
    except discord.HTTPException:
        return

@tasks.loop(hours=1)
async def auto_report_loop():
    await update_report_message()
    await remove_expired_completion_roles()

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Sync error: {e}")

    if not auto_report_loop.is_running():
        auto_report_loop.start()

    try:
        await update_report_message()
    except Exception as e:
        print(f"Initial report update error: {e}")

    try:
        await remove_expired_completion_roles()
    except Exception as e:
        print(f"Initial role cleanup error: {e}")

    print(f"Logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_message(message):
    if message.author.bot:
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

    embed = discord.Embed(
        title=f"🎰 {gacha_def['name']} 結果",
        description=f"{rarity}\n**{name}**",
        color=0xFFD700
    )
    embed.set_image(url=img)
    embed.set_footer(text=f"{cost}HPT消費しました")

    missing = get_missing_characters_for_gacha(interaction.user.id, gacha_def)
    complete_role = await award_completion_role_if_needed(interaction, gacha_def)

    if complete_role:
        embed.add_field(
            name="🎉 コンプ達成",
            value=f"{gacha_def['name']} をコンプしました！\nロール「{complete_role.name}」を付与しました。",
            inline=False
        )
    else:
        if missing:
            embed.add_field(
                name="📘 コンプ状況",
                value=f"残り {len(missing)}種",
                inline=False
            )
        else:
            embed.add_field(
                name="📘 コンプ状況",
                value="コンプ済み",
                inline=False
            )

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

    active_limited = get_active_limited_gachas()
    if not active_limited:
        await interaction.response.send_message(
            "現在開催中の限定ガチャはありません。",
            ephemeral=True
        )
        return

    gacha_def = None

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

    embed = discord.Embed(
        title=f"🎰 {gacha_def['name']} 結果",
        description=f"{rarity}\n**{name}**",
        color=0xFF66CC
    )
    embed.set_image(url=img)
    embed.set_footer(text=f"{cost}HPT消費しました")

    missing = get_missing_characters_for_gacha(interaction.user.id, gacha_def)
    complete_role = await award_completion_role_if_needed(interaction, gacha_def)

    if complete_role:
        embed.add_field(
            name="🎉 コンプ達成",
            value=f"{gacha_def['name']} をコンプしました！\nロール「{complete_role.name}」を付与しました。",
            inline=False
        )
    else:
        if missing:
            embed.add_field(
                name="📘 コンプ状況",
                value=f"残り {len(missing)}種",
                inline=False
            )
        else:
            embed.add_field(
                name="📘 コンプ状況",
                value="コンプ済み",
                inline=False
            )

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
    gacha_def = None

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

    if missing:
        text = "\n".join(missing)
    else:
        text = "コンプ済み"

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

    cur.execute("SELECT user_id, coins FROM users ORDER BY coins DESC LIMIT 10")
    rows = cur.fetchall()

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
