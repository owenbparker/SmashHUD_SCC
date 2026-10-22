"""Settings for the Smash HUD, saved to config.json next to the
project (or next to SmashHUD.exe in the desktop build). Everything
here can be changed from the control panel while the app runs."""

import copy
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = getattr(sys, "frozen", False)
RESOURCE_DIR = getattr(sys, "_MEIPASS", ROOT) if FROZEN else ROOT
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT
WEB_DIR = os.path.join(RESOURCE_DIR, "web")
SPONSOR_DIR = os.path.join(DATA_DIR, "sponsors")   # uploaded sponsor logos (served as sponsors/...)
LOGO_DIR = os.path.join(DATA_DIR, "logos")         # uploaded tournament logos (served as logos/...)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    # Not the League (8777, 8779), VALORANT (8778), CoD (8780), Rocket League
    # (8781) HUD's or the GFX Engine's (8782, 8783) ports.
    "http_host": "127.0.0.1",
    "http_port": 8784,
    "allow_network": False,

    # The crews: their name, and their players in the order they play (the
    # one on stage is shown on the scoreboard; none listed shows the crew's name).
    "left": {"name": "PLAYER 1", "short": "", "color": "#7200ff", "wins": 0, "players": [], "on_stage": 0},
    "right": {"name": "PLAYER 2", "short": "", "color": "#7200ff", "wins": 0, "players": [], "on_stage": 0},
    # Each crew's pool of stocks: how many it starts with and has left, and
    # how many stocks a game is played with.
    "crews": {"stocks": 12, "left": 12, "right": 12, "per_game": 3},
    "best_of": 3,
    "match": "Grand Finals",
    # The logo in the middle of the scoreboard ("logos/x.png"); none shows the Smash Class logo.
    "tournament_logo": "",
    # Sponsor logos in the box at the bottom, in turn, each for this many seconds.
    "sponsors": [],
    "sponsor_seconds": 8,
    "show": {"sponsors": True},
    # Reading the game: where the picture comes from ("device": a capture card
    # or virtual camera by number, "window": a window by its title, "screen":
    # a whole screen by number), and the box around each crew's stock icons
    # ([x0, y0, x1, y1] as fractions of the picture, drawn with full stocks).
    "reader": {"on": False, "source": {"kind": "device", "index": 0, "window": "", "screen": 0},
               "boxes": {"left": None, "right": None}},
    # The SCC GFX Engine this HUD sends its game and series to (see smashhud/enginelink.py).
    "gfx_engine": {"url": "", "name": "", "connected": False},
}

_lock = threading.Lock()


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = _merge(DEFAULTS, json.load(f))
        self.version = 0

    def get(self):
        with _lock:
            return copy.deepcopy(self.data)

    def update(self, changes):
        with _lock:
            self.data = _merge(self.data, changes)
            self.version += 1
            snapshot = copy.deepcopy(self.data)
        self.save(snapshot)
        return snapshot

    def save(self, snapshot=None):
        snapshot = snapshot if snapshot is not None else self.get()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, self.path)


def web_file(rel):
    """Path on disk for a URL path: uploaded sponsors (sponsors/...) and
    tournament logos (logos/...), else the web folder. Paths that climb out are refused."""
    rel = rel.replace("\\", "/").lstrip("/")
    base = WEB_DIR
    for prefix, folder in (("sponsors/", SPONSOR_DIR), ("logos/", LOGO_DIR)):
        if rel.startswith(prefix):
            base, rel = folder, rel[len(prefix):]
    path = os.path.normpath(os.path.join(base, rel))
    if not path.startswith(os.path.normpath(base) + os.sep) or not os.path.isfile(path):
        return None
    return path
