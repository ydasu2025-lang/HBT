from flask import Flask
import threading
import discord
from discord.ext import commands
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

def get_user(user_id):
    cur.execute("SELECT coins, last_post FROM users WHERE user_id=?", (str(user_id),))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (user_id, coins, last_post) VALUES (?, 0, 0)", (str(user_id),))
        conn.commit()
        return 0, 0
    return row

def set_coins(user_id, amount):
    amount = max(0, amount)
    cur.execute("UPDATE users SET coins=? WHERE user_id=?", (amount, str(user_id)))
    conn.commit()

def add_coins(user_id, amount):
    coins, _ = get_user(user_id)
    new_amount = max(0, coins + amount)
    cur.execute("UPDATE users SET coins=? WHERE user_id=?", (new_amount, str(user_id)))
    conn.commit()

def set_last_post(user_id):
    cur.execute("UPDATE users SET last_post=? WHERE user_id=?", (time.time(), str(user_id)))
    conn.commit()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    coins, last = get_user(message.author.id)

    if time.time() - last > 10:
        add_coins(message.author.id, 10)
        set_last_post(message.author.id)

    await bot.process_commands(message)

@bot.command()
async def balance(ctx):
    coins, _ = get_user(ctx.author.id)
    await ctx.send(f"💰 {ctx.author.display_name} のHPT: {coins}")

@bot.command()
async def top(ctx):
    cur.execute("SELECT user_id, coins FROM users ORDER BY coins DESC LIMIT 10")
    rows = cur.fetchall()

    if not rows:
        await ctx.send("まだランキングデータがないよ。")
        return

    lines = []
    for i, (user_id, coins) in enumerate(rows, start=1):
        member = ctx.guild.get_member(int(user_id)) if ctx.guild else None
        name = member.display_name if member else f"User {user_id}"
        lines.append(f"{i}位：{name} - {coins} HPT")

    embed = discord.Embed(title="🏆 HPTランキング", description="\n".join(lines))
    await ctx.send(embed=embed)

GACHA = [
    ("ハズレ犬", "⭐️", 70, "https://picsum.photos/300"),
    ("レア猫", "⭐️⭐️", 25, "https://picsum.photos/301"),
    ("神ドラゴン", "⭐️⭐️⭐️⭐️", 5, "https://picsum.photos/302"),
]

def roll():
    r = random.randint(1, 100)
    total = 0
    for name, rarity, weight, img in GACHA:
        total += weight
        if r <= total:
            return name, rarity, img

@bot.command()
async def gacha(ctx):
    coins, _ = get_user(ctx.author.id)

    if coins < 50:
        await ctx.send("HPTが足りない！50HPT必要です。")
        return

    add_coins(ctx.author.id, -50)

    name, rarity, img = roll()

    embed = discord.Embed(
        title="🎰 ガチャ結果",
        description=f"{rarity}\n**{name}**",
        color=0xFFD700
    )
    embed.set_image(url=img)
    embed.set_footer(text="50HPT消費しました")

    await ctx.send(embed=embed)

if __name__ == "__main__":
    try:
        keep_alive()
        bot.run(TOKEN)
    except Exception:
        traceback.print_exc()
        raise
