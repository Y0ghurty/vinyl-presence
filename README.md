# Vinyl Presence

[![Download](https://img.shields.io/github/v/release/Y0ghurty/vinyl-presence?label=Download&color=ef7a3c)](https://github.com/Y0ghurty/vinyl-presence/releases/latest)
[![Discord](https://img.shields.io/badge/Discord-Vinyl%20Hangout-5865F2?logo=discord&logoColor=white)](https://discord.gg/xHj9Td7MYz)

Spotify-style Discord status for the records on your real turntable.

Pick the record you just put on in a small desktop window. Your Discord profile then shows the album
cover, the track, the artist and a progress bar that moves through each side, so everyone can see you're
spinning actual vinyl:

> **Listening to My Record Player**
> Time
> Pink Floyd
> The Dark Side Of The Moon · Side A · on vinyl

![Vinyl Presence window](docs/screenshot.png)

- **Your own collection:** import the CSV export of your [Discogs](https://www.discogs.com) collection.
- **Fast search:** type a few letters of the artist, album, label, catalog number or year and press
  Enter. Small typos are fine.
- **Sides and tracks:** it knows each side's tracklist and track lengths, moves to the next track by
  itself, and asks you to flip the record when a side ends.
- **Cover view:** browse your collection as a wall of album covers, like flipping through your shelf.
- **Listening stats:** hours listened, your most played records and artists, and hours per month.
- **Now spinning channel:** optionally post what you play to a channel in a Discord server, or, as a
  server owner, run a bot that posts what all your members play.
- **Stays out of the way:** it runs in the tray next to the clock and can start with Windows.
- **A normal Windows app:** nothing to host, no account, no server.

## Download

1. Download **`VinylPresence.exe`** from the [latest release](https://github.com/Y0ghurty/vinyl-presence/releases/latest).
2. Double-click it. The app isn't code-signed, so Windows may say *"Windows protected your PC"*: click
   **More info → Run anyway**.

Your settings, play history and cached covers are stored in `%APPDATA%\Vinyl Presence`.

**Updates:** when a new version comes out, an **Update** button appears at the top of the app. One click
downloads and installs it, and the app restarts by itself. If a record is playing, it keeps playing.

## One-time setup (about 5 minutes)

1. **Create a Discord application.** Discord uses it for Rich Presence. It is not a bot.
   - Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
   - Its name is the title of your status ("Listening to *name*"). Make it say you're using a turntable,
     like `My Record Player`.
   - Copy the **Application ID** from *General Information*.
2. **Upload the vinyl badge** (recommended). In your application, go to **Rich Presence → Art Assets**.
   Scroll down to *Rich Presence Assets* (not the Invite Image), click **Add Image(s)**, upload
   `vinyl.png`, name it `vinyl` and save. *Settings → Show vinyl.png* in the app shows you where the
   file is. It puts a small record on the album cover ("Spinning on my turntable").
3. **Your collection:** type your Discogs username in *Settings → Records* and it syncs by itself
   (at startup and every 6 hours). Or export the CSV on Discogs (*Collection → Export*) and import it.
4. **Start Vinyl Presence.** Settings opens on the first run. Paste the Application ID, click
   *Import Discogs CSV…* and choose your export, then click **Save**. The dot in the top right turns
   green: *Discord connected*.
5. In Discord, make sure *User Settings → Activity Privacy → Share your detected activities with others*
   is on.

## Using it

- Start typing to search. **Enter** plays the top result, **↑ ↓** pick another one, **Esc** clears.
  *Surprise me* picks a random record.
- It starts at side A. Click **B**, **C**, and so on when you put on another side, or double-click any
  track if you dropped the needle somewhere else.
- Clicked play a bit early or late? **−10s** and **+10s** move the timer so the progress bar matches
  the record.
- When a side ends, the app asks **Flip the record?**. You can also have it continue automatically.
- Click **Stop** when you're done. If you forget, the status clears itself 10 minutes after the last side
  ends, or after an hour when track lengths are unknown.
- **List / Covers** switches between the text list and the cover wall. **Stats** shows your listening.
- Closing the window keeps the app running in the tray, so your status stays on. Click the record icon
  next to the clock to open it again, or right-click it and choose **Quit**.

When the track lengths are known, your status shows the current track with a progress bar. When they
aren't, it shows the album with an elapsed timer.

## Settings

| Setting | What it does |
|---|---|
| Card title | The top line of your status: your app's name (default), the artist, the album, or your own text |
| Member list shows | What appears after "Listening to" in server member lists: the artist, the track/album, or the card title |
| "View on Discogs" button | Adds a button to your status that links to the release |
| Vinyl badge asset name | The art asset from setup step 2 |
| Discogs token | Optional. Discogs allows more lookups per minute with it ([get one](https://www.discogs.com/settings/developers)) |
| Continue with the next side | Flip automatically after *n* seconds instead of waiting for you |
| Import Discogs CSV | Load a newer export after you buy records |
| Post what I play | Posts each record you play (after 30 seconds) to a Discord channel through its webhook URL |
| Keep running in the tray | Closing the window hides it to the tray instead of quitting |
| Start with Windows | Starts Vinyl Presence quietly in the tray when you log in |

### Setting up a "now spinning" channel

A server admin opens the channel's settings in Discord (*Edit Channel → Integrations → Webhooks →
New Webhook*), then clicks **Copy Webhook URL**. Paste it under *Settings → Sharing & tray*, tick
**Post what I play** and click **Send test**. Anyone who has a webhook URL can post in that channel, so
only share it with people you trust. For a whole server, use the bot below instead.

### Running a now-spinning bot for your server

For server owners who want *members* to show up in the channel without handing out a webhook. While
your Vinyl Presence is open, a bot watches the members' Discord status and posts the records they play.
Members only type `/nowspinning on` in your server; they never see a secret.

1. In the [Discord Developer Portal](https://discord.com/developers/applications), click **New Application**
   (for example "Vinyl Hangout"), open **Bot**, click **Reset Token** and copy the token.
2. On that same Bot page, switch on **Presence Intent** and **Server Members Intent**, then save.
3. In Vinyl Presence, open *Settings → Server bot*, paste the token, tick **Run the bot** and click **Save**.
4. Click **Invite the bot to your server** (it appears once the bot is online) and add it to your server.
   Use this link rather than the Developer Portal's install link: that one only adds the slash commands,
   not the bot itself, so the bot couldn't see anyone's status or post.
5. In your now-spinning channel, type `/nowspinning channel`. Members join with `/nowspinning on` and
   leave with `/nowspinning off`.

A record is posted once it has played for 30 seconds, under the member's name and avatar, with the cover
and a Discogs link. The bot only runs while your Vinyl Presence is open (it can live in the tray and start
with Windows).

## What goes online

- **Discord:** the app talks to the Discord app on your own PC, the same way games do.
- **Discogs and Deezer:** to look up each record's cover, tracklist and track lengths (vinyl entries on
  Discogs usually have no lengths). Each record is looked up once, and the results are cached.
- **GitHub:** checks once per start whether a newer version is out.
- **Your Discord channel:** only if you turn on *Post what I play*, and only the record's title,
  artist, cover and Discogs link.
- **Server bot:** only if you run one; it connects to Discord as your bot.

Your collection, history and stats never leave your PC.

## Run from source

You need [Python](https://www.python.org/downloads/) 3.10 or newer.

```bash
git clone https://github.com/Y0ghurty/vinyl-presence.git
```

Then double-click `start.bat` (it installs Pillow, pystray and discord.py the first time), or run `pythonw app.py`. When you run it from source, settings and data
are kept in the project folder, and you can also drop your Discogs CSV in there instead of importing it.

| File | What it does |
|---|---|
| `app.py` | The window |
| `core.py` | Collection, search and the player (sides, tracks, timers) |
| `metadata.py` | Looks up covers and tracklists (Discogs, Deezer) |
| `discord_ipc.py` | Talks to the Discord app |
| `covers.py` | The cover wall and its thumbnail cache |
| `stats.py`, `stats_view.py` | Listening stats and the window that shows them |
| `share.py` | Posts "now spinning" messages to a Discord webhook |
| `server_bot.py` | The optional now-spinning bot for server owners |
| `tray.py` | The tray icon and Start with Windows |
| `updater.py` | Checks GitHub for a newer release and lets the exe update itself |
| `version.py` | Version number, set automatically for releases |

## Publishing a new version

Push a tag:

```bash
git tag v1.1.0
```

```bash
git push origin v1.1.0
```

GitHub Actions then builds `VinylPresence.exe` and publishes it as a release with automatic release
notes (see `.github/workflows/release.yml`). Everyone running an older version gets the Update button.
Release assets must keep the name `VinylPresence.exe`; the updater downloads the `.exe` attached to the
latest release.

## Need help?

Join **[Vinyl Hangout](https://discord.gg/xHj9Td7MYz)** on Discord for questions, setup help or ideas, or
just to share what's on your turntable. You can also click **Help** in the app to get there.

Found a bug? [Open an issue](https://github.com/Y0ghurty/vinyl-presence/issues).

## Troubleshooting

- **"Discord isn't running"**: start the Discord desktop app. The browser version of Discord can't show
  Rich Presence. The app reconnects by itself.
- **"Discord rejected the Application ID"**: copy the *Application ID*, not the public key or the
  client secret.
- **No vinyl badge**: check that the image in *Rich Presence Assets* is named exactly `vinyl`. New images
  can take a few minutes to work.
- **Something else**: look in `data\log.txt` (in `%APPDATA%\Vinyl Presence` for the exe).

## License

[MIT](LICENSE)
