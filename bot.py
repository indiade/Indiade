import os
import asyncio
import discord
from dotenv import load_dotenv
from mcstatus import JavaServer

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Channels
WELCOME_CHANNEL_ID = 1558538031730790562
STATUS_CHANNEL_ID = 1561408083140415618

# Minecraft server
MC_SERVER = "play.indiade.net"

intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)

status_message = None

async def get_server_status():
    try:
        server = JavaServer.lookup(MC_SERVER)
        status = server.status()
        return {
            "online": True,
            "players": f"{status.players.online}/{status.players.max}",
            "latency": round(status.latency),
            "version": status.version.name,
            "motd": str(status.description),
        }
    except Exception as e:
        print(f"MC status error: {e}")
        return {"online": False}

async def update_status_embed():
    global status_message
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel:
        print("Status channel not found")
        return

    data = await get_server_status()

    if data["online"]:
        embed = discord.Embed(
            title="Indiade MC Server Status",
            color=discord.Color.green(),
            description="**ONLINE**"
        )
        embed.add_field(name="Název", value="Indiade", inline=True)
        embed.add_field(name="MC Verze", value="1.21", inline=True)
        embed.add_field(name="Vývojová verze", value="1.0 BETA", inline=True)
        embed.add_field(name="Hráči", value=data["players"], inline=True)
        embed.add_field(name="Latence", value=f"{data['latency']} ms", inline=True)
        embed.add_field(name="Verze serveru", value=data["version"], inline=True)
        embed.add_field(name="MOTD", value=data["motd"][:100], inline=False)
        embed.add_field(name="Uptime / Další restart", value="N/A (není dostupné přes query)", inline=False)
    else:
        embed = discord.Embed(
            title="Indiade MC Server Status",
            color=discord.Color.red(),
            description="**OFFLINE**"
        )
        embed.add_field(name="Název", value="Indiade", inline=True)
        embed.add_field(name="MC Verze", value="1.21", inline=True)
        embed.add_field(name="Vývojová verze", value="1.0 BETA", inline=True)

    embed.set_footer(text="Aktualizováno automaticky")

    if status_message:
        try:
            await status_message.edit(embed=embed)
            return
        except:
            pass

    # Find existing status message or send new one
    async for msg in channel.history(limit=20):
        if msg.author == bot.user and msg.embeds and msg.embeds[0].title == "Indiade MC Server Status":
            status_message = msg
            await status_message.edit(embed=embed)
            return

    status_message = await channel.send(embed=embed)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await update_status_embed()
    bot.loop.create_task(status_loop())

async def status_loop():
    while True:
        await asyncio.sleep(120)
        await update_status_embed()

@bot.event
async def on_member_join(member):
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        print(f"Welcome channel {WELCOME_CHANNEL_ID} not found!")
        return

    embed = discord.Embed(
        title="Welcome!",
        description=f"Hey {member.mention}, welcome to **{member.guild.name}**!\n\n"
                    f"Make sure to read the rules and enjoy your stay.",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Member #{member.guild.member_count}")

    await channel.send(embed=embed)

bot.run(TOKEN)
