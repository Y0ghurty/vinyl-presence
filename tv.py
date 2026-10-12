"""Play music videos on a Chromecast, in the YouTube app on your TV, over your home Wi-Fi.

Needs pychromecast. Nothing is hosted: the app talks to the Chromecast directly, the way
the cast button in the YouTube app on your phone does.
"""
import threading
import time

try:
    import pychromecast
    from pychromecast.controllers.youtube import YouTubeController
except ImportError:
    pychromecast = None


SEEK_LEAD = 1.0  # seconds: the jump itself takes about this long on a Chromecast


def available():
    return pychromecast is not None


class TV:
    def __init__(self):
        self.lock = threading.Lock()
        self.cast = None
        self.youtube = None
        self.browser = None
        self.muted_by_us = False
        self.status = {"state": "off"}  # off / connecting / playing / error

    @staticmethod
    def discover(timeout=8):
        """Names of Chromecasts on the network that can show video (speakers and groups are left out)."""
        casts, browser = pychromecast.get_chromecasts(timeout=timeout)
        browser.stop_discovery()

        def rank(c):  # real TVs and Chromecasts first, smart displays (Nest Hub) last
            model = (c.cast_info.model_name or "").lower()
            return (0 if "chromecast" in model or "tv" in model else 2 if "hub" in model or "display" in model else 1,
                    c.cast_info.friendly_name.lower())
        return [c.cast_info.friendly_name for c in sorted(casts, key=rank) if c.cast_info.cast_type == "cast"]

    def _connect(self, name):
        if self.cast is not None and self.cast.cast_info.friendly_name == name:
            return
        self._disconnect()
        casts, browser = pychromecast.get_listed_chromecasts(friendly_names=[name], timeout=10)
        if not casts:
            browser.stop_discovery()
            raise ConnectionError(f'"{name}" wasn\'t found on your network. Is it on, and on the same Wi-Fi?')
        cast = casts[0]
        try:
            cast.wait(timeout=10)  # the connection resolves the device through the browser: keep it running
        except Exception:
            browser.stop_discovery()
            raise
        youtube = YouTubeController()
        cast.register_handler(youtube)
        self.cast, self.youtube, self.browser = cast, youtube, browser

    def play(self, name, video, muted=True, position=None):
        """Start a video ({'id', 'title'}) on the named Chromecast. Blocks for a few seconds.

        position() says where the record is now (seconds, or None if it moved on). Casting takes a
        few seconds and YouTube starts at 0:00, so once the video really plays it's moved there.
        """
        with self.lock:
            self.status = {"state": "connecting", "device": name, "video": video}
            try:
                self._connect(name)
                self.youtube.play_video(video["id"])
                if muted and not self.cast.status.volume_muted:
                    self.cast.set_volume_muted(True)  # the record is the music
                    self.muted_by_us = True
                elif not muted and self.muted_by_us:
                    self.cast.set_volume_muted(False)
                    self.muted_by_us = False
            except Exception as e:
                self.status = {"state": "error", "device": name, "error": str(e)}
                raise
            self.status = {"state": "playing", "device": name, "video": video}
            if position and self._wait_until_playing(video["id"]):
                self._seek(position)

    def _wait_until_playing(self, video_id, timeout=25):
        media = self.cast.media_controller
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = media.status
            if st and st.content_id == video_id and st.player_state == "PLAYING":
                return True
            time.sleep(0.3)
        print(f"[tv] {video_id} didn't start playing within {timeout} s, so it wasn't synced")
        return False

    def _seek(self, position):
        target = position()
        if target is None:
            return  # the record moved on to another song meanwhile
        target += SEEK_LEAD
        media = self.cast.media_controller
        duration = media.status.duration if media.status else None
        if target < 1.5 or (duration and target > duration - 2):
            return
        media.seek(target)
        print(f"[tv] synced the video to {int(target) // 60}:{int(target) % 60:02d}")

    def sync(self, position):
        """Line the video up with the record again (after nudging the timer, or by hand)."""
        with self.lock:
            if self.cast is not None and self.status.get("state") == "playing":
                self._seek(position)

    def stop(self):
        with self.lock:
            if self.cast is not None:
                try:
                    if self.muted_by_us:
                        self.cast.set_volume_muted(False)
                    self.cast.quit_app()
                except Exception:
                    pass
                self.muted_by_us = False
            self.status = {"state": "off"}

    def _disconnect(self):
        if self.cast is not None:
            try:
                self.cast.disconnect(timeout=3)
            except Exception:
                pass
        if self.browser is not None:
            try:
                self.browser.stop_discovery()
            except Exception:
                pass
        self.cast = self.youtube = self.browser = None

    def close(self):
        with self.lock:
            self._disconnect()
