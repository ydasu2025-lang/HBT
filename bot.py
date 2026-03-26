from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "Bot is running"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()
import discord
from discord.ext import commands
import sqlite3
import random
import time
import os

TOKEN = os.getenv("TOKEN")

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
        cur.execute("INSERT INTO users (user_id, coins) VALUES (?, 0)", (str(user_id),))
        conn.commit()
        return 0, 0
    return row

def add_coins(user_id, amount):
    coins, last = get_user(user_id)
    coins += amount
    cur.execute("UPDATE users SET coins=? WHERE user_id=?", (coins, str(user_id)))
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
        await message.channel.send(f"{message.author.display_name} +10HPT")

    await bot.process_commands(message)

@bot.command()
async def balance(ctx):
    coins, _ = get_user(ctx.author.id)
    await ctx.send(f"{ctx.author.display_name} のコイン: {coins}")

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
        await ctx.send("HPTが足りない！")
        return

    add_coins(ctx.author.id, -50)

    name, rarity, img = roll()

    embed = discord.Embed(title="🎰 ガチャ結果")
    embed.description = f"{rarity}\n{name}"
    embed.set_image(url=img)

    await ctx.send(embed=embed)

from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "Bot is running"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()
import discord
from discord.ext import commands
import sqlite3
import random
import time
import os

TOKEN = os.getenv("TOKEN")

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
        cur.execute("INSERT INTO users (user_id, coins) VALUES (?, 0)", (str(user_id),))
        conn.commit()
        return 0, 0
    return row

def add_coins(user_id, amount):
    coins, last = get_user(user_id)
    coins += amount
    cur.execute("UPDATE users SET coins=? WHERE user_id=?", (coins, str(user_id)))
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
        await message.channel.send(f"{message.author.display_name} +10HPT")

    await bot.process_commands(message)

@bot.command()
async def balance(ctx):
    coins, _ = get_user(ctx.author.id)
    await ctx.send(f"{ctx.author.display_name} のコイン: {coins}")

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
        await ctx.send("HPTが足りない！")
        return

    add_coins(ctx.author.id, -50)

    name, rarity, img = roll()

    embed = discord.Embed(title="🎰 ガチャ結果")
    embed.description = f"{rarity}\n{name}"
    embed.set_image(url=img)

    await ctx.send(embed=embed)

keep_allive()
bot.run(TOKEN)
