from flask import Flask
import threading
import discord
from discord.ext import commands
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
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

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

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Slash sync error: {e}")
    print(f"Logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # 画像・動画の拡張子
    allowed_exts = (
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".mp4", ".mov", ".webm", ".mkv"
    )

    has_media = False

    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        filename = attachment.filename.lower()

        if content_type.startswith("image/") or content_type.startswith("video/"):
            has_media = True
            break

        if filename.endswith(allowed_exts):
            has_media = True
            break

    if has_media:
        coins, last = get_user(message.author.id)

        if time.time() - last > 10:
            add_coins(message.author.id, 10)
            set_last_post(message.author.id)

    await bot.process_commands(message)
    
GACHA = [
    ("みゆ", "⭐️", 70, "https://picsum.photos/300"),
    ("ほのか", "⭐️⭐️", 25, "https://picsum.photos/301"),
    ("めい", "⭐️⭐️⭐️⭐️", 5, "https://picsum.photos/302"),
]

def roll():
    r = random.randint(1, 100)
    total = 0
    for name, rarity, weight, img in GACHA:
        total += weight
        if r <= total:
            return name, rarity, img

@bot.tree.command(name="balance", description="自分のHPTを見る")
async def balance(interaction: discord.Interaction):
    coins, _ = get_user(interaction.user.id)
    await interaction.response.send_message(
        f"💰 {interaction.user.display_name} のHPT: {coins}",
        ephemeral=True
    )

@bot.tree.command(name="gacha", description="50HPTでガチャを引く")
async def gacha(interaction: discord.Interaction):
    coins, _ = get_user(interaction.user.id)

    if coins < 50:
        await interaction.response.send_message(
            "HPTが足りない！50HPT必要です。",
            ephemeral=True
        )
        return

    add_coins(interaction.user.id, -50)

    name, rarity, img = roll()

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

if __name__ == "__main__":
    try:
        keep_alive()
        bot.run(TOKEN)
    except Exception:
        traceback.print_exc()
        raise
