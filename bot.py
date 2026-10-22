import os
import sys
import functools
import builtins
print = functools.partial(builtins.print, flush=True)
import json
import asyncio
import discord
from discord import app_commands
from dotenv import load_dotenv
from mcstatus import JavaServer

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Channels
WELCOME_CHANNEL_ID = 1558538031730790562
STATUS_CHANNEL_ID = 1561408083140415618
INVITE_LOG_CHANNEL_ID = 1562867004707176448

# Minecraft server
MC_SERVER = "play.indiade.net"

# Invite counts file (created next to bot.py)
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "invites.json")

intents = discord.Intents.default()
intents.members = True
intents.invites = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

status_message = None
invite_cache = {}   # guild_id -> {code: uses}
vanity_cache = {}   # guild_id -> uses


# ---------- Invite data storage ----------

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"inviters": {}, "joined_by": {}}

def save_data():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)

data = load_data()

def stats_for(user_id):
    s = data["inviters"].setdefault(str(user_id), {"joins": 0, "left": 0, "fake": 0})
    return s

def total(s):
    return s["joins"] - s["left"] - s["fake"]


# ---------- Invite cache ----------

async def cache_guild_invites(guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {i.code: i.uses for i in invites}
    except discord.Forbidden:
        print(f"Missing 'Manage Server' permission on {guild.name} - invite logger disabled.")
        invite_cache[guild.id] = {}
    try:
        vanity = await guild.vanity_invite()
        vanity_cache[guild.id] = vanity.uses if vanity else None
    except (discord.Forbidden, discord.HTTPException):
        vanity_cache[guild.id] = None

async def find_used_invite(guild):
    """Compare invite use counts before and after a join."""
    old = invite_cache.get(guild.id, {})
    try:
        new_invites = await guild.invites()
    except discord.Forbidden:
        return None, "unknown"
    used = None
    for inv in new_invites:
        if inv.uses > old.get(inv.code, 0):
            used = inv
            break
    if used is None:
        # Limited-use invites disappear after being used
        new_codes = {i.code for i in new_invites}
        gone = [c for c in old if c not in new_codes]
        invite_cache[guild.id] = {i.code: i.uses for i in new_invites}
        if len(gone) == 1:
            return gone[0], "gone"
    invite_cache[guild.id] = {i.code: i.uses for i in new_invites}
    if used:
        return used, "invite"
    # Server vanity URL
    try:
        vanity = await guild.vanity_invite()
        if vanity and vanity_cache.get(guild.id) is not None and vanity.uses > vanity_cache[guild.id]:
            vanity_cache[guild.id] = vanity.uses
            return vanity, "vanity"
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None, "unknown"


# ---------- MC status ----------

async def get_server_status():
    try:
        server = await JavaServer.async_lookup(MC_SERVER)
        status = await server.async_status()
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

    data_ = await get_server_status()

    if data_["online"]:
        embed = discord.Embed(title="Indiade MC Server Status", color=discord.Color.green(), description="**ONLINE**")
        embed.add_field(name="Name", value="Indiade", inline=True)
        embed.add_field(name="MC Version", value="1.21", inline=True)
        embed.add_field(name="Dev Version", value="1.0 BETA", inline=True)
        embed.add_field(name="Players", value=data_["players"], inline=True)
        embed.add_field(name="Latency", value=f"{data_['latency']} ms", inline=True)
        embed.add_field(name="Server Version", value=data_["version"], inline=True)
        embed.add_field(name="MOTD", value=data_["motd"][:100] or "-", inline=False)
        embed.add_field(name="Uptime / Next restart", value="N/A (not available via query)", inline=False)
    else:
        embed = discord.Embed(title="Indiade MC Server Status", color=discord.Color.red(), description="**OFFLINE**")
        embed.add_field(name="Name", value="Indiade", inline=True)
        embed.add_field(name="MC Version", value="1.21", inline=True)
        embed.add_field(name="Dev Version", value="1.0 BETA", inline=True)

    embed.set_footer(text="Updated automatically")

    if status_message:
        try:
            await status_message.edit(embed=embed)
            return
        except Exception:
            pass

    async for msg in channel.history(limit=20):
        if msg.author == bot.user and msg.embeds and msg.embeds[0].title == "Indiade MC Server Status":
            status_message = msg
            await status_message.edit(embed=embed)
            return

    status_message = await channel.send(embed=embed)

async def status_loop():
    while True:
        await asyncio.sleep(120)
        try:
            await update_status_embed()
        except Exception as e:
            print(f"Status update error: {e}")


# ---------- Eventy ----------

started = False

@bot.event
async def on_ready():
    global started
    print(f"Logged in as {bot.user}, guilds: {len(bot.guilds)}")
    if not started:
        started = True
        # Sync commands per guild so they appear instantly
        for guild in bot.guilds:
            try:
                tree.copy_global_to(guild=guild)
                cmds = await tree.sync(guild=guild)
                print(f"Slash commands on {guild.name}: {[c.name for c in cmds]}")
            except Exception as e:
                print(f"Slash command sync error on {guild.name}: {e}")
        bot.loop.create_task(status_loop())
    for guild in bot.guilds:
        await cache_guild_invites(guild)
    print("Invite cache ready")
    await update_status_embed()

@bot.event
async def on_invite_create(invite):
    if invite.guild:
        invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

@bot.event
async def on_invite_delete(invite):
    if invite.guild:
        invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

@bot.event
async def on_guild_join(guild):
    await cache_guild_invites(guild)

@bot.event
async def on_member_join(member):
    # Welcome message
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="Welcome!",
            description=f"Hey {member.mention}, welcome to **{member.guild.name}**!\n\n"
                        f"Make sure to read the rules and enjoy your stay.",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        await channel.send(embed=embed)
    else:
        print(f"Welcome channel {WELCOME_CHANNEL_ID} not found!")

    # Invite logger
    invite, kind = await find_used_invite(member.guild)
    log = bot.get_channel(INVITE_LOG_CHANNEL_ID)

    inviter = None
    code = None
    if kind == "invite":
        inviter, code = invite.inviter, invite.code
    elif kind == "gone":
        code = invite
    elif kind == "vanity":
        code = invite.code

    # Accounts younger than 7 days count as suspicious (fake)
    account_age = (discord.utils.utcnow() - member.created_at).days
    fake = account_age < 7

    if inviter and inviter.id != member.id:
        s = stats_for(inviter.id)
        s["joins"] += 1
        if fake:
            s["fake"] += 1
        data["joined_by"][str(member.id)] = {"inviter": inviter.id, "fake": fake}
        save_data()

    if not log:
        print(f"Invite log channel {INVITE_LOG_CHANNEL_ID} not found!")
        return

    if inviter:
        s = stats_for(inviter.id)
        desc = (f"{member.mention} joined using {inviter.mention}'s invite (`{code}`).\n"
                f"{inviter.mention} now has **{total(s)}** invites "
                f"({s['joins']} total, {s['left']} left, {s['fake']} fake).")
    elif kind == "vanity":
        desc = f"{member.mention} joined using the server's vanity URL (`{code}`)."
    elif kind == "gone":
        desc = f"{member.mention} joined using a single-use invite `{code}` (inviter unknown)."
    else:
        desc = f"{member.mention} joined, but the invite used couldn't be determined."

    embed = discord.Embed(title="📥 Member joined", description=desc, color=discord.Color.blurple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account age", value=f"{account_age} days" + (" ⚠️" if fake else ""), inline=True)
    embed.add_field(name="Members", value=str(member.guild.member_count), inline=True)
    await log.send(embed=embed)

@bot.event
async def on_member_remove(member):
    info = data["joined_by"].pop(str(member.id), None)
    inviter_text = "unknown"
    if info:
        s = stats_for(info["inviter"])
        if info.get("fake"):
            s["fake"] = max(0, s["fake"] - 1)
            s["joins"] = max(0, s["joins"] - 1)
        else:
            s["left"] += 1
        save_data()
        inviter_text = f"<@{info['inviter']}> (now has **{total(s)}** invites)"
    log = bot.get_channel(INVITE_LOG_CHANNEL_ID)
    if log:
        embed = discord.Embed(
            title="📤 Member left",
            description=f"**{member}** left the server. Invited by: {inviter_text}.",
            color=discord.Color.red(),
        )
        await log.send(embed=embed)


# ---------- Slash commands ----------

@tree.command(name="invites", description="Show how many people a member invited")
@app_commands.describe(member="Member (empty = you)")
async def invites_cmd(interaction: discord.Interaction, member: discord.Member | None = None):
    member = member or interaction.user
    s = stats_for(member.id)
    await interaction.response.send_message(
        f"{member.mention} has **{total(s)}** invites ({s['joins']} total, {s['left']} left, {s['fake']} fake).")

@tree.command(name="invites-top", description="Invite leaderboard")
async def invites_top(interaction: discord.Interaction):
    rows = sorted(data["inviters"].items(), key=lambda kv: total(kv[1]), reverse=True)[:10]
    if not rows:
        await interaction.response.send_message("Nobody has invited anyone yet.")
        return
    lines = [f"**{i+1}.** <@{uid}> – {total(s)}" for i, (uid, s) in enumerate(rows)]
    embed = discord.Embed(title="🏆 Invite Leaderboard", description="\n".join(lines), color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


bot.run(TOKEN)
