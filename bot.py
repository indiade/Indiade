import os
import sys
import functools
import builtins
print = functools.partial(builtins.print, flush=True)
import json
import asyncio
import io
import time
from datetime import datetime, timezone
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
intents.message_content = True  # needed for ticket transcripts

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
            "players_online": status.players.online,
            "players_max": status.players.max,
            "names": [p.name for p in (status.players.sample or []) if p.name and "§" not in p.name],
            "latency": round(status.latency),
            "version": status.version.name,
            "motd": status.motd.to_plain() if hasattr(status, "motd") else str(status.description),
        }
    except Exception as e:
        print(f"MC status error: {e}")
        return {"online": False}

LOGO_URL = "https://cdn.indiade.com/brand/discord/servers.png"
SERVER_PAGE = "https://indiade.com/servers"
STATUS_TITLE = "Indiade MC Server Status"

def player_bar(online, maximum, size=12):
    if maximum <= 0:
        return "`" + "░" * size + "`"
    filled = round(size * min(online, maximum) / maximum)
    return "`" + "█" * filled + "░" * (size - filled) + "`"

def build_status_embed(d):
    now = discord.utils.utcnow()
    if d["online"]:
        on, mx = d["players_online"], d["players_max"]
        embed = discord.Embed(
            title=STATUS_TITLE,
            url=SERVER_PAGE,
            description="🟢 **Server is ONLINE**\nJump in and play with us!",
            color=0x2BFF88,
            timestamp=now,
        )
        embed.add_field(name="🌐 Server IP", value=f"```{MC_SERVER}```", inline=False)
        embed.add_field(name="👥 Players", value=f"**{on} / {mx}**\n{player_bar(on, mx)}", inline=True)
        embed.add_field(name="📶 Ping", value=f"**{d['latency']} ms**", inline=True)
        embed.add_field(name="🧩 Version", value=f"**{d['version']}**", inline=True)
        if d.get("names"):
            embed.add_field(name="🎮 Online now", value=", ".join(d["names"][:15])[:1000], inline=False)
        if d["motd"].strip():
            embed.add_field(name="💬 MOTD", value=f"*{d['motd'].strip()[:200]}*", inline=False)
    else:
        embed = discord.Embed(
            title=STATUS_TITLE,
            url=SERVER_PAGE,
            description="🔴 **Server is OFFLINE**\nWe'll be back soon. Stay tuned in this channel!",
            color=0xFF2E4D,
            timestamp=now,
        )
        embed.add_field(name="🌐 Server IP", value=f"```{MC_SERVER}```", inline=False)
    embed.add_field(name="🏷️ Minecraft", value="Java 1.21+", inline=True)
    embed.add_field(name="🛠️ Build", value="1.0 BETA", inline=True)
    embed.add_field(name="🔗 Website", value=f"[indiade.com/servers]({SERVER_PAGE})", inline=True)
    embed.set_author(name="Indiade Servers", url=SERVER_PAGE, icon_url=LOGO_URL)
    embed.set_thumbnail(url=LOGO_URL)
    embed.set_footer(text="Auto-updates every 2 minutes • Last update", icon_url=LOGO_URL)
    return embed

async def update_status_embed():
    global status_message
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel:
        print("Status channel not found")
        return

    embed = build_status_embed(await get_server_status())

    if status_message:
        try:
            await status_message.edit(content=None, embed=embed)
            return
        except Exception:
            pass

    async for msg in channel.history(limit=20):
        if msg.author == bot.user and msg.embeds and msg.embeds[0].title == STATUS_TITLE:
            status_message = msg
            await status_message.edit(content=None, embed=embed)
            return

    status_message = await channel.send(embed=embed)

async def status_loop():
    while True:
        await asyncio.sleep(120)
        try:
            await update_status_embed()
        except Exception as e:
            print(f"Status update error: {e}")



LEADERBOARD_TITLE = "🏆 Invite Leaderboard"

def build_leaderboard_embed():
    rows = sorted(data["inviters"].items(), key=lambda kv: total(kv[1]), reverse=True)
    rows = [r for r in rows if r[1]["joins"] > 0][:15]
    medals = ["🥇", "🥈", "🥉"]
    if rows:
        lines = []
        for i, (uid, st) in enumerate(rows):
            pos = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{pos} <@{uid}> **{total(st)}** invites  ·  {st['joins']} joined, {st['left']} left, {st['fake']} fake")
        desc = "\n".join(lines)
    else:
        desc = "Nobody has invited anyone yet. Be the first!"
    all_total = sum(max(0, total(st)) for st in data["inviters"].values())
    embed = discord.Embed(title=LEADERBOARD_TITLE, description=desc, color=0xFFC83D, timestamp=discord.utils.utcnow())
    embed.add_field(name="📊 Total invites", value=f"**{all_total}**", inline=True)
    embed.add_field(name="👥 Inviters", value=f"**{len(rows)}**", inline=True)
    embed.set_footer(text="Updates automatically on every join/leave • Use /invites")
    return embed

leaderboard_message = None

async def update_leaderboard():
    global leaderboard_message
    channel = bot.get_channel(INVITE_LOG_CHANNEL_ID)
    if not channel:
        return
    embed = build_leaderboard_embed()
    if leaderboard_message is None:
        mid = data.get("leaderboard_message_id")
        if mid:
            try:
                leaderboard_message = await channel.fetch_message(mid)
            except Exception:
                leaderboard_message = None
    # Keep it as the latest message: delete the old one and repost below the new log entry
    if leaderboard_message:
        try:
            await leaderboard_message.delete()
        except Exception:
            pass
    leaderboard_message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    data["leaderboard_message_id"] = leaderboard_message.id
    save_data()

# ---------- Events ----------

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
        register_ticket_views()
        bot.loop.create_task(ticket_inactivity_loop())
    for guild in bot.guilds:
        await cache_guild_invites(guild)
    print("Invite cache ready")
    try:
        await update_leaderboard()
    except Exception as e:
        print(f"Leaderboard error: {e}")
    try:
        await update_status_embed()
    except Exception as e:
        print(f"Status embed error: {e}")
    try:
        await setup_ticket_panel()
    except Exception as e:
        print(f"Ticket panel error: {e}")

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
    await update_leaderboard()

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
        await update_leaderboard()


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


# =====================================================================
# ---------- Ticket system ----------
# =====================================================================

SUPPORT_ROLE_IDS = [1558535971748716635, 1558535782401310820, 1558535578843095132]
TICKET_PANEL_CHANNEL_ID = 1562874803231133908
TICKET_LOG_CHANNEL_ID = 1562875188637474926
TICKET_CATEGORY_NAME = "Tickets"
PANEL_TITLE = "🎫 Indiade Support Tickets"
TICKETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.json")

UNCLAIMED_PING_AFTER = 4 * 3600
AUTHOR_REMIND_AFTER = 24 * 3600
AUTHOR_CLOSE_AFTER = 24 * 3600
INACTIVITY_CHECK_EVERY = 600

HIGH_PRIORITY_WORDS = ["hack", "cheat", "grief", "scam", "payment", "refund", "ban", "exploit", "dupe"]
PRIORITY_COLORS = {"High": 0xFF3B4D, "Normal": 0x3BA7FF}

# key -> (label, emoji, slug, [(question, placeholder, min_len, paragraph)])
TICKET_CATEGORIES = {
    "support": ("Support", "🛠️", "support", [
        ("Subject", "Short summary of your issue", 3, False),
        ("Describe the problem", "Tell us as much as you can", 10, True)]),
    "bug": ("Bug Report", "🐛", "bug-report", [
        ("What's the bug?", "Describe what goes wrong", 10, True),
        ("Steps to reproduce", "1. ... 2. ... 3. ...", 10, True),
        ("MC version / where it happened", "e.g. 1.21.4, spawn, /warp shop", 2, False)]),
    "player": ("Player Report", "⚠️", "player-report", [
        ("Player name", "Exact in-game name", 2, False),
        ("What happened?", "Describe the situation", 10, True),
        ("Proof (links)", "Screenshot / video links", 4, True)]),
    "payment": ("Purchase / Payment", "💰", "payment", [
        ("In-game name", "Your Minecraft name", 2, False),
        ("Transaction ID / email", "From your receipt", 3, False),
        ("Describe the issue", "What went wrong with the purchase?", 10, True)]),
    "partner": ("Partnership", "🤝", "partnership", [
        ("Project / community name", "Name of your server, channel, ...", 2, False),
        ("Links & audience size", "Links, member / follower counts", 4, True),
        ("Your proposal", "What do you have in mind?", 20, True)]),
    "minecraft": ("Minecraft Server", "⛏️", "minecraft", [
        ("In-game name", "Your Minecraft name", 2, False),
        ("Subject", "Short summary", 3, False),
        ("Details", "Describe your question or issue", 10, True)]),
    "other": ("Other", "❓", "other", [
        ("Subject", "Short summary", 3, False),
        ("Details", "How can we help?", 10, True)]),
}


def load_tickets():
    try:
        with open(TICKETS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("counter", 0)
    d.setdefault("panel_message_id", None)
    d.setdefault("open", {})       # channel_id(str) -> ticket
    d.setdefault("closed", [])
    d.setdefault("rating_msgs", {})  # message_id(str) -> ticket number
    return d

def save_tickets():
    try:
        tmp = TICKETS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tdata, f, ensure_ascii=False, indent=2)
        os.replace(tmp, TICKETS_FILE)
    except Exception as e:
        print(f"Ticket save error: {e}")

tdata = load_tickets()
closing_channels = set()


# ---------- Pure helpers (unit-tested) ----------

def slugify(text, max_len=100):
    import unicodedata, re
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:max_len].strip("-")

def ticket_channel_name(category_slug, username):
    user = slugify(username) or "user"
    return slugify(f"{category_slug}-{user}", 100) or "ticket"

def detect_priority(*texts):
    import re
    blob = " ".join(t or "" for t in texts).lower()
    # stems like hack/cheat match hacker, cheating...; short words (ban) only as whole words + common suffixes
    pat = r"\b(?:" + "|".join(HIGH_PRIORITY_WORDS) + r")(?:s|ed|ned|ing|ning|er|ers|ers|ing)?\b|\b(?:hack|cheat|grief|scam|exploit|dup)\w*"
    return "High" if re.search(pat, blob) else "Normal"

def inactivity_decision(t, now):
    """Return 'remind_author', 'auto_close' or None.
    Claimed tickets are never auto-closed. Unclaimed: remind the author after 24 h
    without their message, close 24 h after the reminder if they still didn't reply."""
    if t.get("claimed_by"):
        return None
    author_last = t.get("last_author_msg") or t["opened_at"]
    rem = t.get("reminder_sent_at")
    if rem is not None and author_last > rem:
        rem = None  # author replied after the reminder
    if rem is None:
        return "remind_author" if now - author_last >= AUTHOR_REMIND_AFTER else None
    return "auto_close" if now - rem >= AUTHOR_CLOSE_AFTER else None

def fmt_duration(seconds):
    seconds = int(max(0, seconds))
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = ([f"{d}d"] if d else []) + ([f"{h}h"] if h else []) + [f"{m}m"]
    return " ".join(parts)

def build_transcript_html(guild_name, channel_name, ticket, messages):
    """messages: list of dicts {author, avatar, bot, time, content, attachments:[(name,url)], embeds:[str]}"""
    import html as h
    e = h.escape
    cat = TICKET_CATEGORIES.get(ticket.get("category"), ("Unknown", "", "", []))
    rows = []
    for m in messages:
        body = e(m.get("content") or "").replace("\n", "<br>")
        att = "".join(f'<div class="att">📎 <a href="{e(u)}" target="_blank">{e(n)}</a></div>'
                      for n, u in m.get("attachments", []))
        emb = "".join(f'<div class="embed">{e(s).replace(chr(10), "<br>")}</div>' for s in m.get("embeds", []))
        tag = '<span class="tag">BOT</span>' if m.get("bot") else ""
        rows.append(
            f'<div class="msg"><img class="av" src="{e(m.get("avatar") or "")}" alt="">'
            f'<div class="c"><div class="hd"><span class="name">{e(m.get("author", "?"))}</span>{tag}'
            f'<span class="time">{e(m.get("time", ""))}</span></div>'
            f'<div class="body">{body}</div>{att}{emb}</div></div>')
    info = (f"Ticket #{ticket.get('number')} · {e(cat[1])} {e(cat[0])} · Priority: {e(ticket.get('priority', ''))}"
            f" · Messages: {len(messages)}")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Transcript – {e(channel_name)}</title>
<style>
body{{background:#313338;color:#dbdee1;font-family:'gg sans','Segoe UI',Helvetica,Arial,sans-serif;margin:0}}
header{{background:#1e1f22;padding:20px 28px;border-bottom:1px solid #111}}
header h1{{margin:0;font-size:20px;color:#fff}} header p{{margin:6px 0 0;color:#949ba4;font-size:14px}}
.wrap{{padding:12px 20px}} .msg{{display:flex;padding:8px;border-radius:6px}} .msg:hover{{background:#2e3035}}
.av{{width:40px;height:40px;border-radius:50%;margin-right:14px;background:#5865f2;flex-shrink:0}}
.hd{{display:flex;align-items:center;gap:8px}} .name{{color:#fff;font-weight:600}}
.tag{{background:#5865f2;color:#fff;font-size:10px;padding:1px 5px;border-radius:3px}}
.time{{color:#949ba4;font-size:12px}} .body{{margin-top:2px;line-height:1.4;word-wrap:break-word}}
.att a{{color:#00a8fc}} .embed{{border-left:4px solid #5865f2;background:#2b2d31;padding:8px 12px;margin-top:6px;border-radius:4px;font-size:14px}}
footer{{color:#949ba4;font-size:12px;padding:16px 28px}}
</style></head><body>
<header><h1>#{e(channel_name)} — {e(guild_name)}</h1><p>{info}</p></header>
<div class="wrap">{''.join(rows) or '<p>No messages.</p>'}</div>
<footer>Generated by Indiade Bot</footer></body></html>"""


# ---------- Discord helpers ----------

def is_staff(member):
    return isinstance(member, discord.Member) and any(r.id in SUPPORT_ROLE_IDS for r in member.roles)

def support_mentions():
    return " ".join(f"<@&{r}>" for r in SUPPORT_ROLE_IDS)

def get_ticket(channel_id):
    return tdata["open"].get(str(channel_id))

def build_ticket_embed(t):
    label, emoji, _, _ = TICKET_CATEGORIES[t["category"]]
    em = discord.Embed(title=f"{emoji} {label} — Ticket #{t['number']}",
                       description="Thanks for opening a ticket! Our staff will be with you shortly.\n"
                                   "Please add any extra details or screenshots below.",
                       color=PRIORITY_COLORS.get(t["priority"], 0x3BA7FF),
                       timestamp=datetime.fromtimestamp(t["opened_at"], timezone.utc))
    em.add_field(name="👤 Author", value=f"<@{t['author_id']}>", inline=True)
    em.add_field(name="📂 Category", value=label, inline=True)
    em.add_field(name="🚦 Priority", value=("🔴 High" if t["priority"] == "High" else "🔵 Normal"), inline=True)
    em.add_field(name="🙋 Claimed by", value=f"<@{t['claimed_by']}>" if t.get("claimed_by") else "*Unclaimed*", inline=True)
    for q, a in t.get("answers", []):
        em.add_field(name=q, value=(a or "-")[:1024], inline=False)
    em.set_footer(text=f"Ticket #{t['number']} • Opened")
    return em

async def send_log(embed, file=None):
    ch = bot.get_channel(TICKET_LOG_CHANNEL_ID)
    if not ch:
        print("Ticket log channel not found")
        return None
    try:
        return await ch.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        print(f"Ticket log error: {e}")
        return None

async def safe_reply(interaction, text):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except Exception:
        pass

async def get_tickets_category(guild):
    cat = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    if cat:
        return cat
    return await guild.create_category(TICKET_CATEGORY_NAME, overwrites={
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)})


class SafeView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        print(f"Ticket view error: {error!r}")
        msg = "❌ I'm missing permissions to do that." if isinstance(error, discord.Forbidden) \
            else "❌ Something went wrong. Please try again or contact staff."
        await safe_reply(interaction, msg)


# ---------- Panel / opening ----------

class PanelView(SafeView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open a ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ticket:open")
    async def open_btn(self, interaction, button):
        await interaction.response.send_message("**Choose a category for your ticket:**",
                                                view=CategorySelectView(), ephemeral=True)

class CategorySelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=v[0], emoji=v[1], value=k) for k, v in TICKET_CATEGORIES.items()]
        super().__init__(placeholder="Select a category…", options=opts, min_values=1, max_values=1)

    async def callback(self, interaction):
        key = self.values[0]
        existing = find_open_ticket(interaction.user.id, key)
        if existing:
            await interaction.response.send_message(
                f"You already have an open **{TICKET_CATEGORIES[key][0]}** ticket: <#{existing}>", ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal(key))

class CategorySelectView(SafeView):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(CategorySelect())

def find_open_ticket(user_id, category):
    for cid, t in tdata["open"].items():
        if t["author_id"] == user_id and t["category"] == category:
            if bot.get_channel(int(cid)):
                return int(cid)
    return None

class TicketModal(discord.ui.Modal):
    def __init__(self, key):
        label, emoji, _, qs = TICKET_CATEGORIES[key]
        super().__init__(title=f"{label} ticket"[:45], timeout=900)
        self.key = key
        self.inputs = []
        for q, ph, mn, para in qs[:5]:
            ti = discord.ui.TextInput(label=q[:45], placeholder=ph[:100], required=True, min_length=mn,
                                      max_length=1000 if para else 100,
                                      style=discord.TextStyle.paragraph if para else discord.TextStyle.short)
            self.add_item(ti)
            self.inputs.append((q, ti))

    async def on_error(self, interaction, error):
        print(f"Ticket modal error: {error!r}")
        await safe_reply(interaction, "❌ Could not create the ticket (missing permissions?). Please contact staff.")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        answers = [(q, ti.value.strip()) for q, ti in self.inputs]
        ch = await create_ticket(interaction, self.key, answers)
        if ch:
            await interaction.followup.send(f"✅ Your ticket has been created: {ch.mention}", ephemeral=True)

async def create_ticket(interaction, key, answers):
    guild, user = interaction.guild, interaction.user
    if guild is None:
        await safe_reply(interaction, "Tickets can only be opened in the server.")
        return None
    existing = find_open_ticket(user.id, key)
    if existing:
        await safe_reply(interaction, f"You already have an open ticket in this category: <#{existing}>")
        return None
    label, emoji, slug, _ = TICKET_CATEGORIES[key]
    try:
        category = await get_tickets_category(guild)
        allow = dict(view_channel=True, send_messages=True, attach_files=True, read_message_history=True, embed_links=True)
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
              user: discord.PermissionOverwrite(**allow),
              guild.me: discord.PermissionOverwrite(manage_channels=True, manage_permissions=True,
                                                    manage_messages=True, **allow)}
        for rid in SUPPORT_ROLE_IDS:
            role = guild.get_role(rid)
            if role:
                ow[role] = discord.PermissionOverwrite(**allow)
        tdata["counter"] += 1
        number = tdata["counter"]
        save_tickets()
        ch = await guild.create_text_channel(ticket_channel_name(slug, user.name), category=category,
                                             overwrites=ow, topic=f"Ticket #{number} • {label} • {user} ({user.id})")
    except discord.Forbidden:
        await safe_reply(interaction, "❌ I don't have permission to create ticket channels. Please contact staff.")
        return None
    now = time.time()
    t = {"number": number, "channel_id": ch.id, "author_id": user.id, "author_name": str(user),
         "category": key, "priority": detect_priority(*[a for _, a in answers]), "answers": answers,
         "opened_at": now, "claimed_by": None, "last_author_msg": None, "last_staff_msg": None,
         "staff_ping_sent": False, "reminder_sent_at": None, "opening_msg_id": None}
    tdata["open"][str(ch.id)] = t
    save_tickets()
    try:
        msg = await ch.send(content=f"{user.mention} • {support_mentions()}", embed=build_ticket_embed(t),
                            view=TicketControlView(),
                            allowed_mentions=discord.AllowedMentions(roles=True, users=True))
        t["opening_msg_id"] = msg.id
        save_tickets()
    except Exception as e:
        print(f"Ticket opening message error: {e}")
    em = discord.Embed(title="📬 Ticket opened", color=PRIORITY_COLORS[t["priority"]], timestamp=discord.utils.utcnow())
    em.add_field(name="Ticket", value=f"#{number} • {ch.mention}", inline=True)
    em.add_field(name="Author", value=user.mention, inline=True)
    em.add_field(name="Category", value=f"{emoji} {label}", inline=True)
    em.add_field(name="Priority", value=t["priority"], inline=True)
    await send_log(em)
    return ch


# ---------- In-ticket controls ----------

async def claim_ticket(interaction):
    t = get_ticket(interaction.channel_id)
    if not t:
        return await safe_reply(interaction, "This is not an open ticket channel.")
    if not is_staff(interaction.user):
        return await safe_reply(interaction, "❌ Only support staff can claim tickets.")
    if t.get("claimed_by"):
        return await safe_reply(interaction, f"This ticket is already claimed by <@{t['claimed_by']}>.")
    t["claimed_by"] = interaction.user.id
    t["claimed_at"] = time.time()
    save_tickets()
    await interaction.response.send_message(f"🙋 {interaction.user.mention} has claimed this ticket and will help you.")
    try:
        if t.get("opening_msg_id"):
            m = await interaction.channel.fetch_message(t["opening_msg_id"])
            await m.edit(embed=build_ticket_embed(t))
    except Exception as e:
        print(f"Ticket embed update error: {e}")
    em = discord.Embed(title="🙋 Ticket claimed", color=0xFFC83D, timestamp=discord.utils.utcnow(),
                       description=f"Ticket #{t['number']} ({interaction.channel.mention}) claimed by {interaction.user.mention}")
    await send_log(em)

class TicketControlView(SafeView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="ticket:claim")
    async def claim_btn(self, interaction, button):
        await claim_ticket(interaction)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close_btn(self, interaction, button):
        t = get_ticket(interaction.channel_id)
        if not t:
            return await safe_reply(interaction, "This ticket is already closed.")
        if not (is_staff(interaction.user) or interaction.user.id == t["author_id"]):
            return await safe_reply(interaction, "❌ You can't close this ticket.")
        await interaction.response.send_message("Are you sure you want to close this ticket?",
                                                view=CloseConfirmView(), ephemeral=True)

    @discord.ui.button(label="Add user", emoji="➕", style=discord.ButtonStyle.secondary, custom_id="ticket:add")
    async def add_btn(self, interaction, button):
        if not get_ticket(interaction.channel_id):
            return await safe_reply(interaction, "This is not an open ticket channel.")
        if not is_staff(interaction.user):
            return await safe_reply(interaction, "❌ Only support staff can add users.")
        await interaction.response.send_message("Select a user to add:", view=AddUserView(), ephemeral=True)

class CloseConfirmView(SafeView):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.edit_message(content="🔒 Closing ticket…", view=None)
        await close_ticket(interaction.channel, interaction.user, "Closed via button")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Close cancelled.", view=None)

class AddUserView(SafeView):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Pick a user…", min_values=1, max_values=1)
    async def pick(self, interaction, select):
        await add_user_to_ticket(interaction, select.values[0])

async def add_user_to_ticket(interaction, user):
    try:
        await interaction.channel.set_permissions(user, view_channel=True, send_messages=True,
                                                  attach_files=True, read_message_history=True, embed_links=True)
    except discord.Forbidden:
        return await safe_reply(interaction, "❌ I don't have permission to edit this channel.")
    await safe_reply(interaction, f"✅ Added {user.mention}.")
    try:
        await interaction.channel.send(f"➕ {user.mention} was added to the ticket by {interaction.user.mention}.")
    except Exception:
        pass


# ---------- Closing / rating ----------

def serialize_message(m):
    embeds = []
    for e in m.embeds:
        parts = [e.title or ""] + ([e.description] if e.description else []) + \
                [f"{f.name}: {f.value}" for f in e.fields]
        embeds.append("\n".join(p for p in parts if p)[:1500])
    content = m.content or ""
    if m.type != discord.MessageType.default and not content and not embeds and not m.attachments:
        content = f"[{m.type.name}]"
    return {"author": m.author.display_name + f" ({m.author.name})", "avatar": m.author.display_avatar.url,
            "bot": m.author.bot, "time": m.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "content": content, "attachments": [(a.filename, a.url) for a in m.attachments], "embeds": embeds}

async def close_ticket(channel, closed_by, reason):
    t = get_ticket(channel.id)
    if not t or channel.id in closing_channels:
        return
    closing_channels.add(channel.id)
    try:
        msgs = []
        try:
            async for m in channel.history(limit=None, oldest_first=True):
                msgs.append(serialize_message(m))
        except Exception as e:
            print(f"Transcript history error: {e}")
        html_doc = build_transcript_html(channel.guild.name, channel.name, t, msgs)
        now = time.time()
        rec = dict(t, closed_at=now, closed_by=getattr(closed_by, "id", None), reason=reason,
                   rating=None, log_msg_id=None, channel_name=channel.name)
        tdata["open"].pop(str(channel.id), None)
        tdata["closed"].append(rec)
        save_tickets()

        label, emoji, _, _ = TICKET_CATEGORIES[t["category"]]
        em = discord.Embed(title="🔒 Ticket closed", color=0x99AAB5, timestamp=discord.utils.utcnow())
        em.add_field(name="Ticket", value=f"#{t['number']} • `{channel.name}`", inline=True)
        em.add_field(name="Author", value=f"<@{t['author_id']}>", inline=True)
        em.add_field(name="Category", value=f"{emoji} {label}", inline=True)
        em.add_field(name="Closed by", value=closed_by.mention if closed_by else "🤖 System", inline=True)
        em.add_field(name="Claimed by", value=f"<@{t['claimed_by']}>" if t.get("claimed_by") else "—", inline=True)
        em.add_field(name="Duration", value=fmt_duration(now - t["opened_at"]), inline=True)
        em.add_field(name="Reason", value=reason[:1024], inline=False)
        em.add_field(name="Rating", value="*Waiting for the author…*", inline=False)
        f = discord.File(io.BytesIO(html_doc.encode("utf-8")), filename=f"transcript-{t['number']}-{channel.name}.html")
        lm = await send_log(em, file=f)
        if lm:
            rec["log_msg_id"] = lm.id
            save_tickets()

        # Rating prompt for the author
        rate_em = discord.Embed(title="⭐ How did we do?",
                                description=f"Your ticket **#{t['number']}** ({label}) on **{channel.guild.name}** was closed.\n"
                                            "Please rate the support you received (1–5 stars).",
                                color=0xFFC83D)
        wait = 5
        sent = None
        try:
            author = channel.guild.get_member(t["author_id"]) or await bot.fetch_user(t["author_id"])
            sent = await author.send(embed=rate_em, view=RatingView())
        except Exception:
            try:
                sent = await channel.send(content=f"<@{t['author_id']}>", embed=rate_em, view=RatingView())
                wait = 30
            except Exception:
                sent = None
        if sent:
            tdata["rating_msgs"][str(sent.id)] = t["number"]
            save_tickets()
        try:
            await channel.send(f"🔒 This ticket will be deleted in {wait} seconds.")
        except Exception:
            pass
        await asyncio.sleep(wait)
        try:
            await channel.delete(reason=f"Ticket #{t['number']} closed: {reason}")
        except Exception as e:
            print(f"Ticket channel delete error: {e}")
    finally:
        closing_channels.discard(channel.id)

class RatingView(SafeView):
    def __init__(self):
        super().__init__(timeout=None)
        for i in range(1, 6):
            b = discord.ui.Button(label=str(i), emoji="⭐", style=discord.ButtonStyle.secondary,
                                  custom_id=f"ticket:rate:{i}")
            b.callback = self.make_cb(i)
            self.add_item(b)

    def make_cb(self, stars):
        async def cb(interaction):
            num = tdata["rating_msgs"].get(str(interaction.message.id))
            rec = next((r for r in tdata["closed"] if r["number"] == num), None)
            if rec is None:
                return await safe_reply(interaction, "This rating is no longer available.")
            if interaction.user.id != rec["author_id"]:
                return await safe_reply(interaction, "Only the ticket author can rate it.")
            if rec.get("rating"):
                return await safe_reply(interaction, "You've already rated this ticket. Thanks!")
            rec["rating"] = stars
            tdata["rating_msgs"].pop(str(interaction.message.id), None)
            save_tickets()
            try:
                await interaction.response.edit_message(
                    content=f"Thanks for your feedback! You rated us {'⭐' * stars}", embed=None, view=None)
            except Exception:
                pass
            await update_close_log_rating(rec)
        return cb

async def update_close_log_rating(rec):
    ch = bot.get_channel(TICKET_LOG_CHANNEL_ID)
    if not ch or not rec.get("log_msg_id"):
        return
    try:
        m = await ch.fetch_message(rec["log_msg_id"])
        em = m.embeds[0]
        for i, f in enumerate(em.fields):
            if f.name == "Rating":
                em.set_field_at(i, name="Rating", value=f"{'⭐' * rec['rating']} ({rec['rating']}/5)", inline=False)
        await m.edit(embed=em)
    except Exception as e:
        print(f"Rating log update error: {e}")


# ---------- Activity tracking & inactivity loop ----------

async def ticket_on_message(message):
    if message.author.bot or message.guild is None:
        return
    t = get_ticket(message.channel.id)
    if not t:
        return
    now = time.time()
    if message.author.id == t["author_id"]:
        t["last_author_msg"] = now
        t["reminder_sent_at"] = None
    elif is_staff(message.author):
        t["last_staff_msg"] = now
        t["staff_ping_sent"] = False
    else:
        return
    save_tickets()

async def ticket_inactivity_loop():
    while True:
        await asyncio.sleep(INACTIVITY_CHECK_EVERY)
        try:
            now = time.time()
            for cid, t in list(tdata["open"].items()):
                ch = bot.get_channel(int(cid))
                if ch is None:
                    continue
                action = inactivity_decision(t, now)
                if action == "ping_staff":
                    t["staff_ping_sent"] = True
                    save_tickets()
                    await ch.send(f"⏰ {support_mentions()} This ticket is still waiting for staff.",
                                  allowed_mentions=discord.AllowedMentions(roles=True))
                    await send_log(discord.Embed(title="⏰ Unclaimed ticket reminder", color=0xFF9F1C,
                                                 description=f"Ticket #{t['number']} ({ch.mention}) has had no staff reply for 4+ hours."))
                elif action == "remind_author":
                    t["reminder_sent_at"] = now
                    save_tickets()
                    await ch.send(f"👋 <@{t['author_id']}> Do you still need help? Please reply, otherwise this "
                                  f"ticket will be closed automatically in 24 hours.")
                elif action == "auto_close":
                    bot.loop.create_task(close_ticket(ch, None, "Inactive author (unclaimed)"))
        except Exception as e:
            print(f"Ticket inactivity loop error: {e}")


# ---------- Panel ----------

def build_panel_embed():
    em = discord.Embed(title=PANEL_TITLE, color=0x5865F2,
                       description="Need help? Open a private ticket and our staff team will assist you.\n\n"
                                   + "\n".join(f"{v[1]} **{v[0]}**" for v in TICKET_CATEGORIES.values())
                                   + "\n\nClick the button below, pick a category and fill in the short form.")
    em.set_author(name="Indiade Support", icon_url=LOGO_URL)
    em.set_thumbnail(url=LOGO_URL)
    em.set_footer(text="Please don't open tickets without a reason • One ticket per category")
    return em

async def setup_ticket_panel():
    ch = bot.get_channel(TICKET_PANEL_CHANNEL_ID)
    if not ch:
        print("Ticket panel channel not found")
        return
    em, view = build_panel_embed(), PanelView()
    msg = None
    if tdata.get("panel_message_id"):
        try:
            msg = await ch.fetch_message(tdata["panel_message_id"])
        except Exception:
            msg = None
    if msg is None:
        try:
            async for m in ch.history(limit=50):
                if m.author == bot.user and m.embeds and m.embeds[0].title == PANEL_TITLE:
                    msg = m
                    break
        except Exception:
            pass
    try:
        if msg:
            await msg.edit(embed=em, view=view)
        else:
            msg = await ch.send(embed=em, view=view)
        tdata["panel_message_id"] = msg.id
        save_tickets()
    except Exception as e:
        print(f"Ticket panel error: {e}")

def register_ticket_views():
    bot.add_view(PanelView())
    bot.add_view(TicketControlView())
    bot.add_view(RatingView())


# ---------- /ticket commands ----------

ticket_group = app_commands.Group(name="ticket", description="Ticket staff commands", guild_only=True)

async def staff_in_ticket(interaction, need_ticket=True):
    if not is_staff(interaction.user):
        await safe_reply(interaction, "❌ Only support staff can use this command.")
        return None
    t = get_ticket(interaction.channel_id)
    if need_ticket and not t:
        await safe_reply(interaction, "❌ This command only works inside an open ticket channel.")
        return None
    return t or {}

@ticket_group.command(name="add", description="Add a user to this ticket")
async def ticket_add(interaction: discord.Interaction, user: discord.Member):
    if await staff_in_ticket(interaction) is not None:
        await add_user_to_ticket(interaction, user)

@ticket_group.command(name="remove", description="Remove a user from this ticket")
async def ticket_remove(interaction: discord.Interaction, user: discord.Member):
    t = await staff_in_ticket(interaction)
    if t is None:
        return
    if user.id == t["author_id"]:
        return await safe_reply(interaction, "❌ You can't remove the ticket author.")
    try:
        await interaction.channel.set_permissions(user, overwrite=None)
    except discord.Forbidden:
        return await safe_reply(interaction, "❌ I don't have permission to edit this channel.")
    await interaction.response.send_message(f"➖ {user.mention} was removed from the ticket.")

@ticket_group.command(name="rename", description="Rename this ticket channel")
async def ticket_rename(interaction: discord.Interaction, name: str):
    if await staff_in_ticket(interaction) is None:
        return
    new = slugify(name) or "ticket"
    try:
        await interaction.channel.edit(name=new)
    except discord.Forbidden:
        return await safe_reply(interaction, "❌ I don't have permission to rename this channel.")
    except discord.HTTPException:
        return await safe_reply(interaction, "❌ Rename failed (Discord limits renames to 2 per 10 minutes).")
    await interaction.response.send_message(f"✏️ Channel renamed to `{new}`.")

@ticket_group.command(name="close", description="Close this ticket")
async def ticket_close(interaction: discord.Interaction, reason: str | None = None):
    if await staff_in_ticket(interaction) is None:
        return
    await interaction.response.send_message("🔒 Closing ticket…")
    await close_ticket(interaction.channel, interaction.user, reason or "No reason given")

@ticket_group.command(name="claim", description="Claim this ticket")
async def ticket_claim(interaction: discord.Interaction):
    await claim_ticket(interaction)

def compute_ticket_stats(d):
    closed = d["closed"]
    durations = [r["closed_at"] - r["opened_at"] for r in closed if r.get("closed_at")]
    ratings = [r["rating"] for r in closed if r.get("rating")]
    per_cat, claimers = {}, {}
    for r in closed + list(d["open"].values()):
        per_cat[r["category"]] = per_cat.get(r["category"], 0) + 1
        if r.get("claimed_by"):
            claimers[r["claimed_by"]] = claimers.get(r["claimed_by"], 0) + 1
    return {"open": len(d["open"]), "closed": len(closed),
            "avg_resolution": sum(durations) / len(durations) if durations else None,
            "avg_rating": sum(ratings) / len(ratings) if ratings else None, "ratings": len(ratings),
            "per_category": per_cat,
            "top_claimers": sorted(claimers.items(), key=lambda kv: kv[1], reverse=True)[:5]}

@ticket_group.command(name="stats", description="Ticket statistics")
async def ticket_stats(interaction: discord.Interaction):
    if await staff_in_ticket(interaction, need_ticket=False) is None:
        return
    s = compute_ticket_stats(tdata)
    em = discord.Embed(title="📊 Ticket statistics", color=0x5865F2, timestamp=discord.utils.utcnow())
    em.add_field(name="📂 Open", value=str(s["open"]), inline=True)
    em.add_field(name="🔒 Closed", value=str(s["closed"]), inline=True)
    em.add_field(name="⏱️ Avg resolution", value=fmt_duration(s["avg_resolution"]) if s["avg_resolution"] is not None else "—", inline=True)
    em.add_field(name="⭐ Avg rating", value=f"{s['avg_rating']:.2f}/5 ({s['ratings']} ratings)" if s["avg_rating"] else "—", inline=True)
    em.add_field(name="🗂️ Per category", inline=False, value="\n".join(
        f"{TICKET_CATEGORIES[k][1]} {TICKET_CATEGORIES[k][0]}: **{v}**" for k, v in s["per_category"].items()
        if k in TICKET_CATEGORIES) or "—")
    em.add_field(name="🙋 Top claimers", inline=False, value="\n".join(
        f"**{i+1}.** <@{uid}> – {n}" for i, (uid, n) in enumerate(s["top_claimers"])) or "—")
    await interaction.response.send_message(embed=em, ephemeral=True)

tree.add_command(ticket_group)

@tree.error
async def on_app_command_error(interaction, error):
    print(f"Slash command error: {error!r}")
    orig = getattr(error, "original", error)
    await safe_reply(interaction, "❌ I'm missing permissions to do that." if isinstance(orig, discord.Forbidden)
                     else "❌ Something went wrong while running this command.")

@bot.event
async def on_message(message):
    try:
        await ticket_on_message(message)
    except Exception as e:
        print(f"on_message error: {e}")


if __name__ == "__main__":
    bot.run(TOKEN)
