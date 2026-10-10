import os
import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Channel ID for welcome messages
WELCOME_CHANNEL_ID = 1558538031730790562

intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

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
