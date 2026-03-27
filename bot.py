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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

conn = sqlite3.connect("data.db")
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

def log_gacha(user_id: int, character_name: str, rarity: str):
    cur.execute(
        "INSERT INTO gacha_logs (user_id, character_name, rarity, created_at) VALUES (?, ?, ?, ?)",
        (str(user_id), character_name, rarity, time.time())
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

# ここを集計を表示したいテキストチャンネルIDに変える
REPORT_CHANNEL_ID = 1487008334358773842

GACHA = [
    ("みゆ", "S", 25, "https://cdn.discordapp.com/attachments/1486776583858425911/1486834490017055010/S.png?ex=69c6f206&is=69c5a086&hm=69ea5c80115bc07d31794aeefad633b5a099eb68336ce3fa79ff63ba8ac83f22&"),
    ("りみ", "S", 25, "https://cdn.discordapp.com/attachments/1486776583858425911/1486840365032935446/S_1.png?ex=69c6f77f&is=69c5a5ff&hm=260cee99ea95db450ce7dc8a71308bb0ae6bc04a0eff5412cae66247c91b5d7d&"),
    ("さえ", "S", 25, "https://cdn.discordapp.com/attachments/1486863251525603478/1486871101710663770/S_3.png?ex=69c7141f&is=69c5c29f&hm=86c5c46921441db38ca19811c6e693c90fb0227b2046e85f84467e97553c0d2a&"),
    ("ふうあ", "S", 25, "https://cdn.discordapp.com/attachments/1486863251525603478/1486876158749315155/S_2.png?ex=69c718d5&is=69c5c755&hm=4c361ef76e57c20fc3d8bc99484613366ee32455ecaf2184e6df3c0f36062542&"),
]

def roll():
    r = random.randint(1, 100)
    total = 0
    for name, rarity, weight, img in GACHA:
        total += weight
        if r <= total:
            return name, rarity, img

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

    cur.execute("""
        SELECT character_name, COUNT(*)
        FROM gacha_logs
        GROUP BY character_name
        ORDER BY COUNT(*) DESC, character_name ASC
    """)
    rows = cur.fetchall()

    lines = [
        "🎰 ガチャ総合結果",
        f"総ガチャ回数: {total_count}回",
        f"ガチャを引いた人数: {user_count}人",
        ""
    ]

    if not rows:
        lines.append("まだデータなし")
    else:
        for name, count in rows:
            lines.append(f"{name}: {count}回")

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
            add_coins(message.author.id, media_count * 10)
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

@bot.tree.command(name="gacha", description="50HPTでガチャを引く")
async def gacha(interaction: discord.Interaction):
    if interaction.channel_id not in ALLOWED_COMMAND_CHANNELS:
        await interaction.response.send_message(
            "このコマンドは指定チャンネルで使ってください。",
            ephemeral=True
        )
        return

    coins, _ = get_user(interaction.user.id)

    if coins < 50:
        await interaction.response.send_message(
            f"HPTが足りない！今は {coins} HPT、50HPT必要、チャンネルに画像を投稿してHPTをGET",
            ephemeral=True
        )
        return

    add_coins(interaction.user.id, -50)

    name, rarity, img = roll()
    log_gacha(interaction.user.id, name, rarity)

    embed = discord.Embed(
        title="🎰 ガチャ結果",
        description=f"{rarity}\n**{name}**",
        color=0xFFD700
    )
    embed.set_image(url=img)
    embed.set_footer(text="50HPT消費しました")

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
