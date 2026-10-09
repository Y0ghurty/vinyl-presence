"""Now-spinning bot for a Discord server, run by the server owner's Vinyl Presence.

Members don't need any secret: the bot sees their Discord status. When it shows
a record from Vinyl Presence for 30 seconds, the bot posts it in the chosen
channel. Members opt in with /nowspinning on; an admin picks the channel with
/nowspinning channel. Needs discord.py, and the bot's "Presence" and "Server
Members" intents switched on in the Discord Developer Portal.
"""
import asyncio
import json
import re
import threading
import time

try:
    import discord
    from discord import app_commands
except ImportError:
    discord = None

from metadata import _get_json, clean_name

POST_AFTER_SECONDS = 30
REPOST_AFTER_SECONDS = 3 * 3600
ACCENT = 0xEF7A3C
# View Channel + Send Messages + Embed Links
INVITE_PERMISSIONS = 1024 + 2048 + 16384
DISCOGS_RELEASE = re.compile(r"discogs\.com/release/(\d+)")
MEDIUM = re.compile(r"^on (vinyl|CD|cassette|shellac)$")


def available():
    return discord is not None


def invite_url(app_id):
    """Adds the bot user itself (scope "bot"), not only its slash commands."""
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            f"&scope=bot+applications.commands&permissions={INVITE_PERMISSIONS}")


# ---------------------------------------------------------------- reading a member's status

def vinyl_activity(activities):
    """The member's Vinyl Presence activity, if they have one."""
    for a in activities or ():
        if discord is None or not isinstance(a, discord.Activity) or a.type != discord.ActivityType.listening:
            continue
        assets = a.assets or {}
        links = " ".join(x or "" for x in (assets.get("large_url"), a.details_url))
        parts = [p.strip() for p in (assets.get("large_text") or "").split("·")]
        if (DISCOGS_RELEASE.search(links) or any(MEDIUM.match(p) for p in parts)
                or assets.get("small_text") == "Spinning on my turntable" or "View on Discogs" in (a.buttons or [])):
            return a
    return None


def _describe(a):
    """Short human description of any activity, for the log and /nowspinning status."""
    kind = getattr(getattr(a, "type", None), "name", "") or ""
    if kind == "custom":
        return "a custom status"
    return f"{kind} {getattr(a, 'name', '') or ''}".strip() or "an activity"


def record_from_activity(a):
    """What's playing, as far as the status tells: {'key', 'release_id', 'title', 'artist', 'cover', 'url'}."""
    assets = a.assets or {}
    url = assets.get("large_url") or a.details_url or ""
    m = DISCOGS_RELEASE.search(url)
    parts = [p.strip() for p in (assets.get("large_text") or "").split("·")]
    # While a track plays, the 3rd line is "Album · Side A · on vinyl"; otherwise the 1st line is the album.
    first = parts[0] if parts else ""
    in_track_mode = first and not re.match(r"^(Side\b|on\b|Record finished|Disc\b|All$)", first)
    title = first if in_track_mode else (a.details or "")
    image = assets.get("large_image") or ""
    if image.startswith("mp:"):  # Discord's proxy for outside images
        cover = f"https://media.discordapp.net/{image[3:]}"
    else:
        cover = image if image.startswith("https://") else None
    release_id = int(m.group(1)) if m else None
    return {"key": release_id or f"{a.state}|{title}".lower(), "release_id": release_id, "title": title,
            "artist": a.state or "", "cover": cover, "url": f"https://www.discogs.com/release/{release_id}"
            if release_id else None}


def discogs_details(release_id):
    """Title, artist, year, label, format and cover straight from Discogs, for a complete post."""
    d = _get_json(f"https://api.discogs.com/releases/{release_id}")
    images = d.get("images") or []
    primary = next((i for i in images if i.get("type") == "primary"), images[0] if images else {})
    fmt = d.get("formats") or [{}]
    return {
        "title": d.get("title") or "",
        "artist": clean_name(d.get("artists_sort") or ""),
        "year": str(d.get("year") or "") if d.get("year") else "",
        "label": clean_name(((d.get("labels") or [{}])[0]).get("name") or ""),
        "format": ", ".join([*(fmt[0].get("descriptions") or [])][:3]),
        "cover": primary.get("uri"),
    }


# ---------------------------------------------------------------- the bot

class NowSpinningBot(discord.Client if discord else object):
    def __init__(self, store):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.presences = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.store = store
        self.tree = app_commands.CommandTree(self)
        self.pending = {}  # (guild id, member id) -> (record key, task)
        self.last_seen = {}  # (guild id, member id) -> what we last logged, so the log isn't flooded
        self._add_commands()

    def _add_commands(self):
        group = app_commands.Group(name="nowspinning", description="Post the records you play with Vinyl Presence")

        @group.command(name="on", description="Post the records I play in the now-spinning channel")
        async def turn_on(interaction: discord.Interaction):
            if await self._not_in_server(interaction):
                return
            g = self.store.guild(interaction.guild_id)
            if interaction.user.id not in g["members"]:
                g["members"].append(interaction.user.id)
                self.store.save()
            where = f"<#{g['channel']}>" if g.get("channel") else "the now-spinning channel (an admin still has to pick it)"
            await interaction.response.send_message(
                f"You're in! Records you play with Vinyl Presence will be posted in {where}. "
                "Turn it off any time with /nowspinning off, or check what I see with /nowspinning status.",
                ephemeral=True)
            member = interaction.guild.get_member(interaction.user.id)
            if member:
                self._consider(member, "just opted in")  # a record that's already playing counts too

        @group.command(name="status", description="What the bot can see of your Vinyl Presence right now")
        async def status(interaction: discord.Interaction):
            if await self._not_in_server(interaction):
                return
            await interaction.response.send_message(self._status_text(interaction), ephemeral=True)

        @group.command(name="off", description="Stop posting the records I play")
        async def turn_off(interaction: discord.Interaction):
            if await self._not_in_server(interaction):
                return
            g = self.store.guild(interaction.guild_id)
            if interaction.user.id in g["members"]:
                g["members"].remove(interaction.user.id)
                self.store.save()
            await interaction.response.send_message("Done, your records won't be posted anymore.", ephemeral=True)

        @group.command(name="channel", description="Admins: post now-spinning records in this channel")
        @app_commands.default_permissions(manage_guild=True)
        async def set_channel(interaction: discord.Interaction):
            if await self._not_in_server(interaction):
                return
            g = self.store.guild(interaction.guild_id)
            g["channel"] = interaction.channel_id
            self.store.save()
            await interaction.response.send_message(
                "Now-spinning records will be posted in this channel. Members join in with /nowspinning on.",
                ephemeral=True)

        self.tree.add_command(group)

    async def setup_hook(self):
        await self.tree.sync()  # global copy; per-server copies below appear instantly

    async def on_ready(self):
        print(f"[bot] online as {self.user} in {len(self.guilds)} server(s)")
        if not self.guilds:
            print(f"[bot] not in any server yet. Add it with this link (the Developer Portal's install link "
                  f"only adds the commands): {invite_url(self.application_id)}")
        for guild in self.guilds:
            await self._sync_guild(guild)
            self._consider_all(guild, "already playing when the bot started")

    async def on_guild_join(self, guild):
        print(f"[bot] joined {guild.name}")
        await self._sync_guild(guild)
        self._consider_all(guild, "already playing when the bot joined")

    async def _not_in_server(self, interaction):
        """Commands also work where only the app's commands were installed, but then the bot can't see or post."""
        if interaction.guild_id and self.get_guild(interaction.guild_id):
            return False
        await interaction.response.send_message(
            "I'm not really in this server yet: only my commands were added, so I can't see anyone's status or "
            "post. The server owner can fix it by adding me with this link (it adds the bot itself): "
            f"{invite_url(self.application_id)}", ephemeral=True)
        return True

    def _consider_all(self, guild, reason):
        g = self.store.guilds.get(str(guild.id)) or {}
        for member_id in g.get("members", []):
            member = guild.get_member(member_id)
            if member:
                self._consider(member, reason)

    def _status_text(self, interaction):
        g = self.store.guild(interaction.guild_id)
        member = interaction.guild.get_member(interaction.user.id)
        lines = [f"Channel: <#{g['channel']}>" if g.get("channel")
                 else "Channel: not set yet. An admin types /nowspinning channel in the channel to use."]
        opted_in = interaction.user.id in g["members"]
        lines.append("You: opted in ✅" if opted_in else "You: not opted in. Type /nowspinning on to join.")
        activities = member.activities if member else ()
        act = vinyl_activity(activities)
        if act:
            r = record_from_activity(act)
            lines.append(f"I can see you playing **{r['title']}** by {r['artist']} with Vinyl Presence.")
            slot = (interaction.guild_id, interaction.user.id)
            if slot in self.pending and self.pending[slot][0] == r["key"]:
                lines.append("It'll be posted once it has played for 30 seconds.")
            elif self._recently_posted(g, interaction.user.id, r["key"]):
                lines.append("It's already been posted.")
        elif activities:
            lines.append("I can see your status, but no Vinyl Presence record in it: "
                         + ", ".join(_describe(a) for a in activities) + ".")
        else:
            lines.append("I can't see any activity in your status. Is Vinyl Presence playing a record? Also check "
                         "that your status isn't Invisible and that Settings → Activity Privacy → Share your "
                         "detected activities is on.")
        return "\n".join(lines)

    @staticmethod
    def _recently_posted(g, member_id, key):
        last = (g.get("posted") or {}).get(str(member_id))
        return bool(last) and last["key"] == str(key) and time.time() - last["at"] < REPOST_AFTER_SECONDS

    async def _sync_guild(self, guild):
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except discord.HTTPException as e:
            print(f"[bot] couldn't add commands to {guild.name}: {e}")

    async def on_presence_update(self, before, after):
        self._consider(after, "status changed")

    def _log_once(self, member, text):
        slot = (member.guild.id, member.id)
        if self.last_seen.get(slot) != text:
            self.last_seen[slot] = text
            print(f"[bot] {member.display_name}: {text}")

    def _consider(self, member, reason):
        """Start the 30-second wait when an opted-in member's status shows a (new) record."""
        g = self.store.guilds.get(str(member.guild.id))
        if not g or not g.get("channel") or member.id not in g["members"]:
            return
        act = vinyl_activity(member.activities)
        slot = (member.guild.id, member.id)
        current = self.pending.get(slot)
        if not act:
            if current:
                current[1].cancel()
                del self.pending[slot]
            shown = ", ".join(_describe(a) for a in member.activities) or "no activity"
            self._log_once(member, f"no Vinyl Presence record in their status ({shown})")
            return
        record = record_from_activity(act)
        if current and current[0] == record["key"]:
            return  # same record, already waiting (track or side changes don't matter)
        if self._recently_posted(g, member.id, record["key"]):
            self._log_once(member, f"{record['artist']} - {record['title']} was already posted")
            return
        if current:
            current[1].cancel()
        self._log_once(member, f"{record['artist']} - {record['title']} ({reason}); posting in "
                               f"{POST_AFTER_SECONDS} s if it keeps playing")
        self.pending[slot] = (record["key"], asyncio.create_task(self._post_later(member.guild, member.id, record)))

    async def _post_later(self, guild, member_id, record):
        try:
            await asyncio.sleep(POST_AFTER_SECONDS)
        except asyncio.CancelledError:
            return
        slot = (guild.id, member_id)
        if self.pending.get(slot, (None,))[0] == record["key"]:
            del self.pending[slot]
        member = guild.get_member(member_id)
        act = vinyl_activity(member.activities) if member else None
        if not act or record_from_activity(act)["key"] != record["key"]:
            return  # they switched records or stopped within 30 seconds
        g = self.store.guild(guild.id)
        channel = guild.get_channel(g.get("channel") or 0)
        if channel is None:
            print("[bot] the now-spinning channel is gone; type /nowspinning channel in the new one")
            return
        try:
            await channel.send(embed=await self._embed(member, record))
        except discord.HTTPException as e:
            print(f"[bot] couldn't post in #{channel} (does the bot have View Channel, Send Messages and "
                  f"Embed Links there?): {e}")
            return
        g.setdefault("posted", {})[str(member_id)] = {"key": str(record["key"]), "at": time.time()}
        self.store.save()
        self._log_once(member, f"posted {record['artist']} - {record['title']}")

    async def _embed(self, member, record):
        info = {"title": record["title"], "artist": record["artist"], "year": "", "label": "", "format": "",
                "cover": record["cover"]}
        if record["release_id"]:
            try:
                info.update({k: v for k, v in (await asyncio.to_thread(discogs_details, record["release_id"])).items()
                             if v})
            except Exception as e:
                print(f"[bot] Discogs lookup failed, using the status text: {e}")
        details = " · ".join(x for x in (info["year"], info["label"], info["format"]) if x)
        embed = discord.Embed(title=info["title"][:256] or "A record", url=record["url"], color=ACCENT,
                              description=f"**{discord.utils.escape_markdown(info['artist'])}**"
                                          + (f"\n{discord.utils.escape_markdown(details)}" if details else ""),
                              timestamp=discord.utils.utcnow())
        embed.set_author(name=f"{member.display_name} is spinning", icon_url=member.display_avatar.url)
        if info["cover"]:
            embed.set_image(url=info["cover"])
        embed.set_footer(text="Vinyl Presence")
        return embed


class Store:
    """Per server: the channel to post in and who opted in. Saved in data/bot.json."""

    def __init__(self, path):
        self.path = path
        try:
            self.guilds = json.loads(path.read_text("utf-8")).get("guilds", {})
        except (OSError, ValueError):
            self.guilds = {}

    def guild(self, guild_id):
        return self.guilds.setdefault(str(guild_id), {"channel": None, "members": []})

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"guilds": self.guilds}, indent=2), "utf-8")
        tmp.replace(self.path)


# ---------------------------------------------------------------- running it next to the app

class BotRunner:
    """Runs the bot on its own thread and event loop, so the window stays responsive."""

    def __init__(self, store_path):
        self.store_path = store_path
        self.status = {"state": "off"}
        self._loop = self._client = self._thread = None

    def start(self, token):
        self.stop()
        if not available():
            self.status = {"state": "missing"}
            return
        self.status = {"state": "connecting"}
        self._thread = threading.Thread(target=self._run, args=(token,), daemon=True, name="server-bot")
        self._thread.start()

    def _run(self, token):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = NowSpinningBot(Store(self.store_path))
        self._loop, self._client = loop, client

        async def main():
            async with client:
                await client.login(token)
                self.status = {"state": "online", "app_id": client.application_id}
                await client.connect()

        try:
            loop.run_until_complete(main())
            self.status = {"state": "off"}
        except discord.LoginFailure:
            self.status = {"state": "bad_token"}
        except discord.PrivilegedIntentsRequired:
            self.status = {"state": "intents", "app_id": client.application_id}
        except Exception as e:
            self.status = {"state": "error", "error": str(e)}
        finally:
            print(f"[bot] stopped ({self.status['state']})")
            loop.close()

    def details(self):
        """Live status for the Settings window."""
        s = dict(self.status)
        c = self._client
        if s["state"] == "online" and c is not None and c.is_ready():
            s.update(name=str(c.user), servers=len(c.guilds),
                     members=sum(len(g["members"]) for g in c.store.guilds.values()),
                     channels=sum(1 for g in c.store.guilds.values() if g.get("channel")))
        return s

    def invite_url(self):
        app_id = self.status.get("app_id")
        return invite_url(app_id) if app_id else None

    def stop(self):
        if self._client and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._client.close(), self._loop).result(timeout=5)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        self._loop = self._client = self._thread = None
        self.status = {"state": "off"}
