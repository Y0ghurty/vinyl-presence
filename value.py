"""What your collection is worth, from Discogs marketplace prices.

Off unless you switch it on (Settings → Records). For every record the app looks up the lowest price it's
for sale at on Discogs right now: slowly, in the background (Discogs allows 25 lookups a minute, and the
record-info lookups go first), and it keeps the prices for a week. With a Discogs token, Discogs' own
estimate of the collection (minimum, median, maximum) is shown as well.
"""
import ctypes
import json
import threading
import time

from metadata import RateLimited, fetch_collection_value, fetch_price

CURRENCIES = ("EUR", "USD", "GBP", "CAD", "AUD", "JPY", "CHF", "MXN", "BRL", "NZD", "SEK", "ZAR")
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$",
           "BRL": "R$", "MXN": "MX$"}
KEEP = 7 * 86400  # how long a price is good for


def default_currency():
    """The currency of Windows' region settings, when Discogs knows it."""
    try:
        buf = ctypes.create_unicode_buffer(16)
        if ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x15, buf, 16) and buf.value in CURRENCIES:  # LOCALE_SINTLSYMBOL
            return buf.value
    except (AttributeError, OSError):
        pass
    return "USD"


def money(amount, currency):
    symbol = SYMBOLS.get(currency, currency + " ")
    return f"{symbol}{amount:,.0f}" if amount >= 100 else f"{symbol}{amount:,.2f}"


class CollectionValue:
    def __init__(self, core, path):
        self.core, self.path = core, path
        try:
            with open(path, encoding="utf-8") as f:
                self.prices = json.load(f)  # record id -> {"p": price or None, "n": copies for sale, "c": currency, "at"}
        except (OSError, ValueError):
            self.prices = {}
        self.estimate = None  # Discogs' own {"minimum", "median", "maximum"}, with a token
        self.busy = False
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="value").start()

    def currency(self):
        return self.core.config.get("value_currency") or default_currency()

    def _fresh(self, rid, currency):
        p = self.prices.get(str(rid))
        return bool(p) and p.get("c") == currency and time.time() - p.get("at", 0) < KEEP

    def _loop(self):
        time.sleep(30)  # after startup and the collection sync
        while True:
            if self.core.config.get("value_enabled"):
                try:
                    self._update()
                except (OSError, ValueError) as e:
                    print(f"[value] {e}")
                finally:
                    self.busy = False
            self.wake.wait(3600)
            self.wake.clear()

    def _update(self):
        cfg = self.core.config
        currency, token = self.currency(), cfg.get("discogs_token", "").strip()
        if token and cfg.get("discogs_username", "").strip():
            try:
                self.estimate = fetch_collection_value(cfg["discogs_username"].strip(), token)
            except (OSError, ValueError, RateLimited):
                self.estimate = None  # the token isn't this user's, or Discogs is busy: just the prices then
        todo = [r for r in list(self.core.records.values()) if not self._fresh(r["id"], currency)]
        self.busy = bool(todo)
        for i, rec in enumerate(todo):
            while self.core.library.pending():  # record info first: they share Discogs' rate limit
                time.sleep(10)
            if not cfg.get("value_enabled") or self.currency() != currency:
                break  # switched off, or another currency picked: start over
            while True:
                try:
                    price, for_sale = fetch_price(rec["id"], currency, token or None)
                    break
                except RateLimited:
                    time.sleep(60)
            self.prices[str(rec["id"])] = {"p": price, "n": for_sale, "c": currency, "at": int(time.time())}
            if i % 20 == 19:
                self._save()
            time.sleep(1.2 if token else 2.6)
        self._save()
        if todo:
            print(f"[value] looked up {len(todo)} prices on Discogs")

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.prices, f)
        except OSError:
            pass

    def price(self, rid):
        """(lowest price, copies for sale) of a record in the chosen currency, or None if not looked up yet."""
        p = self.prices.get(str(rid))
        return (p["p"], p["n"]) if p and p.get("c") == self.currency() else None

    def summary(self):
        currency = self.currency()
        records = list(self.core.records.values())
        looked_up, priced = 0, []
        for rec in records:
            p = self.prices.get(str(rec["id"]))
            if p and p.get("c") == currency:
                looked_up += 1
                if p.get("p") is not None:
                    priced.append((rec, p["p"], p.get("n", 0)))
        priced.sort(key=lambda x: -x[1])
        return {"currency": currency, "total": sum(p for _r, p, _n in priced), "priced": len(priced),
                "looked_up": looked_up, "records": len(records), "top": priced[:15],
                "estimate": self.estimate, "busy": self.busy}
