"""Updates: find out if GitHub has a newer release, and let the .exe replace itself.

A running .exe can't overwrite itself, so after downloading the new one next to it
we start a tiny hidden PowerShell helper and quit. The helper waits until this
app has fully exited, moves the new exe into place and starts it again.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from metadata import USER_AGENT
from version import GITHUB_REPO, VERSION


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def can_self_update():
    return getattr(sys, "frozen", False) and sys.platform == "win32"


def check():
    """The newest release if it's newer than this one: {'tag', 'page', 'exe_url', 'exe_size'}, else None."""
    if not GITHUB_REPO:
        return None
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        release = json.load(resp)
    tag = release.get("tag_name") or ""
    if version_tuple(tag) <= version_tuple(VERSION):
        return None
    exe = next((a for a in release.get("assets") or [] if a.get("name", "").lower().endswith(".exe")), {})
    return {"tag": tag, "page": release.get("html_url"),
            "exe_url": exe.get("browser_download_url"), "exe_size": exe.get("size")}


def download(update, progress):
    """Download the new exe next to the running one. Calls progress(percent). Returns its path."""
    exe = Path(sys.executable)
    new = exe.with_name(exe.name + ".new")
    req = urllib.request.Request(update["exe_url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, open(new, "wb") as f:
            total = int(resp.headers.get("Content-Length") or update["exe_size"] or 0)
            done, last = 0, -1
            while chunk := resp.read(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else 0
                if pct != last:
                    progress(pct)
                    last = pct
        with open(new, "rb") as f:
            looks_like_exe = f.read(2) == b"MZ"
        if not looks_like_exe or (update["exe_size"] and new.stat().st_size != update["exe_size"]):
            raise ValueError("The download was incomplete. Please try again.")
    except BaseException:
        new.unlink(missing_ok=True)
        raise
    return new


def _ps(s):
    return "'" + str(s).replace("'", "''") + "'"


def restart_into(new):
    """Start the helper that swaps in `new` once this app has exited. Quit the app right after."""
    exe = Path(sys.executable)
    script = (
        f"$exe = {_ps(exe)}; $new = {_ps(new)}; "
        # The .exe runs as two processes (unpacker + app); wait for both to close.
        "Get-Process | Where-Object { $_.Path -eq $exe } | ForEach-Object { $null = $_.WaitForExit(20000) }; "
        "for ($i = 0; $i -lt 20; $i++) { "
        "try { Move-Item -LiteralPath $new -Destination $exe -Force -ErrorAction Stop; break } "
        "catch { Start-Sleep -Milliseconds 500 } }; "
        "Start-Process -FilePath $exe"
    )
    # Make the new exe start fresh instead of inheriting this bundle's unpack settings.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                     env=env, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)


def cleanup():
    """Remove a half-finished download left behind by an interrupted update."""
    if can_self_update():
        Path(sys.executable + ".new").unlink(missing_ok=True)
