"""The running app: the crews' score (from the stock reader, the control
panel or the demo), and the web server for the scoreboard and the control
panel."""

import base64
import json
import mimetypes
import os
import random
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import capture
from .config import DATA_DIR, LOGO_DIR, SPONSOR_DIR, web_file
from .crews import SIDES
from .enginelink import EngineLink, save_logo_bytes, save_sponsors
from .log import log, since
from .reader import StockReader
from .updater import Updater, clean_up_old_update

mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/svg+xml", ".svg")

PAGES = {"/": "/control", "/start": "/control", "/control": "control.html", "/scoreboard": "scoreboard.html",
         "/overlay": "scoreboard.html"}
SIDE_NAMES = {"left": "Left crew", "right": "Right crew"}


class HudApp:
    def __init__(self, config, demo=False):
        self.config = config
        self.updater = Updater()
        self.server = None
        self.notice = None        # something the operator should check, from the reader
        self.flash = {s: 0 for s in SIDES}   # goes up each time a crew loses a stock (the scoreboard pops)
        self.version = 0
        self.demo = None
        self._cond = threading.Condition()
        self._lock = threading.RLock()
        self.reader = StockReader(config, self.on_reader)
        # The SCC GFX Engine: the crews' score and series go to it; its teams can come here.
        self.engine = EngineLink("Smash HUD", "smash", self._engine_snapshot, self._engine_apply_global,
                                 load=lambda: self.config.get().get("gfx_engine"),
                                 save=lambda d: self.config.update({"gfx_engine": d}), log=log)
        if demo:
            self.set_demo(True)

    # --- the crews' score -----------------------------------------------------------

    def on_reader(self, events):
        for e in events:
            if e[0] == "lost":
                self.lose(e[1], e[2], "read")
            elif e[0] == "out":
                self.next_player(e[1], "read")
            elif e[0] == "check":
                self.notice = e[1]
                log(e[1], "warn")
                self.changed()

    def lose(self, side, n=1, how="manual"):
        """A crew lost n stocks (negative: gave back)."""
        with self._lock:
            c = self.config.get()["crews"]
            left = max(0, min(int(c["stocks"]), int(c[side]) - int(n)))
            if left == c[side]:
                return
            self.config.update({"crews": {side: left}})
            if n > 0:
                self.flash[side] += 1
        if how == "read":
            log(f"{SIDE_NAMES[side]} lost {'a stock' if n == 1 else f'{n} stocks'}: {left} left")
            if left == 0:
                log(f"{SIDE_NAMES[side]} is out of stocks")
        self.changed()

    def set_stocks(self, side, value):
        c = self.config.get()["crews"]
        self.config.update({"crews": {side: max(0, min(int(c["stocks"]), int(value)))}})
        self.changed()

    def reset_stocks(self, total=None):
        """Both crews back to a full pool (a new crew battle)."""
        c = self.config.get()["crews"]
        total = max(1, min(99, int(total or c["stocks"])))
        self.config.update({"crews": {"stocks": total, "left": total, "right": total},
                            "left": {"on_stage": 0}, "right": {"on_stage": 0}})
        self.reader.reset()
        self.notice = None
        log(f"New crew battle: {total} stocks each")
        self.changed()

    def swap_sides(self):
        """The crews change sides: their names, colours, players, who's on
        stage, games won and stocks left."""
        with self._lock:
            cfg = self.config.get()
            c = cfg["crews"]
            self.config.update({"left": cfg["right"], "right": cfg["left"],
                                "crews": {"left": c["right"], "right": c["left"]}})
            self.flash = {"left": self.flash["right"], "right": self.flash["left"]}
        log(f"Sides swapped: {cfg['right']['name'] or 'right crew'} on the left")
        self.changed()

    def next_player(self, side, how="manual", step=1):
        cfg = self.config.get()[side]
        players = [p for p in cfg["players"] if p.strip()]
        if not players:
            return
        at = max(0, min(len(players) - 1, int(cfg["on_stage"]) + step))
        if at != cfg["on_stage"]:
            self.config.update({side: {"on_stage": at}})
            if how == "read":
                log(f"{SIDE_NAMES[side]}: {players[at]} is up")
            self.changed()

    def set_demo(self, on):
        if self.demo:
            self.demo.set()
            self.demo = None
        if on:
            self.demo = threading.Event()
            threading.Thread(target=self._demo, args=(self.demo,), daemon=True).start()
            log("Demo crew battle started")
        else:
            log("Demo crew battle stopped")
        self.changed()

    def _demo(self, stop):
        """A made-up crew battle: a stock lost every few seconds."""
        while not stop.wait(3.5):
            c = self.config.get()["crews"]
            if not c["left"] or not c["right"]:
                stop.wait(4)
                self.reset_stocks()
                continue
            side = random.choice(SIDES)
            self.lose(side, 1, "read")
            per_game = int(c["per_game"] or 3)
            if (c["stocks"] - c[side] + 1) % per_game == 0:
                self.next_player(side, "read")

    # --- what the pages see -------------------------------------------------------------

    def shown_name(self, side, cfg=None):
        """The name on the scoreboard: the crew's player on stage, or the crew's name."""
        t = (cfg or self.config.get())[side]
        players = [p for p in t["players"] if p.strip()]
        if players:
            return players[min(int(t["on_stage"]), len(players) - 1)]
        return t["name"]

    def info(self):
        cfg = self.config.get()
        port = self.server.port if self.server else cfg["http_port"]
        lan = lan_ip() if cfg.get("allow_network") else None
        return {"port": port, "lan_url": f"http://{lan}:{port}" if lan else None,
                "network": bool(cfg.get("allow_network")), "demo": bool(self.demo), "notice": self.notice,
                "reader": self.reader.info(), "update": self.updater.info(), "data_dir": DATA_DIR}

    def snapshot(self):
        return {"config": self.config.get(), "app": self.info(), "scoreboard": self.scoreboard()}

    def scoreboard(self):
        cfg = self.config.get()
        out = {"match": cfg["match"], "best_of": int(cfg["best_of"]), "sponsors": cfg["sponsors"],
               "logo": cfg["tournament_logo"] or "assets/logo.png",
               "logo_size": cfg["logo_size"], "logo_y": cfg["logo_y"],
               "sponsor_seconds": cfg["sponsor_seconds"], "show": cfg["show"], "stocks": cfg["crews"]["stocks"]}
        for side in SIDES:
            t = cfg[side]
            out[side] = {"name": self.shown_name(side, cfg), "crew": t["name"], "color": t["color"],
                         "stocks": cfg["crews"][side], "wins": int(t["wins"] or 0), "flash": self.flash[side]}
        return out

    # --- the SCC GFX Engine -------------------------------------------------------------

    def _engine_snapshot(self):
        """What the GFX Engine gets: the crews as flat fields, the series, and the scoreboard page."""
        cfg = self.config.get()
        f = {"match": cfg["match"], "best_of": int(cfg["best_of"]), "stocks_per_crew": cfg["crews"]["stocks"],
             "stocks_per_game": cfg["crews"]["per_game"]}
        for side in SIDES:
            t = cfg[side]
            players = [p for p in t["players"] if p.strip()]
            f[f"{side}_name"] = t["name"]
            f[f"{side}_short"] = t["short"]
            f[f"{side}_player"] = self.shown_name(side, cfg)
            f[f"{side}_stocks"] = cfg["crews"][side]
            f[f"{side}_series"] = int(t["wins"] or 0)
            for n, p in enumerate(players[:8], 1):
                f[f"{side}_p{n}_name"] = p
        series = {side: {"name": cfg[side]["name"], "short": cfg[side]["short"], "score": int(cfg[side]["wins"] or 0),
                         "color": cfg[side]["color"]} for side in SIDES}
        series["best_of"] = int(cfg["best_of"])
        hud = {"port": self.server.port if self.server else cfg["http_port"], "network": bool(cfg.get("allow_network")),
               "graphics": [{"id": "scoreboard", "name": "Crews scoreboard", "path": "/scoreboard", "kind": "overlay"}]}
        return {"fields": f, "series": series, "hud": hud}

    def _engine_apply_global(self, g, fetch):
        """The GFX Engine's teams (names, short names, colours, their players) and sponsors into the HUD."""
        changes, done = {}, []
        rosters = {}
        try:   # (the teams' players, from the engine's team list)
            sides = json.loads(fetch(self.engine.url + "/api/roster/links")).get("sides") or {}
            rosters = {s: [m["name"] or m["ign"] for m in (sides.get(s) or {}).get("members") or [] if m["name"] or m["ign"]]
                       for s in SIDES}
        except Exception as e:
            log(f"Couldn't fetch the teams' players from the GFX Engine: {e}", "warn")
        for side in SIDES:
            t = g.get(side) or {}
            team = {}
            if t.get("name"):
                team["name"] = t["name"]
            if t.get("short"):
                team["short"] = t["short"][:8]
            if rosters.get(side):
                team["players"] = rosters[side]
                team["on_stage"] = 0
            if team:
                changes[side] = team
                done.append(f"{side}: {team.get('name') or team.get('short')}"
                            + (f" ({len(team['players'])} players)" if team.get("players") else ""))
        if g.get("tournament_logo_url"):
            try:
                name = save_logo_bytes(LOGO_DIR, "engine-tournament", fetch(g["tournament_logo_url"]))
                changes["tournament_logo"] = f"logos/{name}"
                done.append("tournament logo")
            except Exception as e:
                log(f"Couldn't fetch the tournament logo from the GFX Engine: {e}", "warn")
        sponsors, secs = save_sponsors(g, fetch, SPONSOR_DIR, "sponsors/", log)
        if sponsors:
            changes["sponsors"] = sponsors
            if secs:
                changes["sponsor_seconds"] = secs
            done.append(f"{len(sponsors)} sponsor{'s' if len(sponsors) != 1 else ''}")
        if g.get("best_of"):
            changes["best_of"] = int(g["best_of"])
        if g.get("stage"):
            changes["match"] = g["stage"]
        if not done:
            raise ValueError("The engine's Global configuration has no team names, tournament logo or sponsors yet")
        self.config.update(changes)
        self.changed()
        return ", ".join(done)

    # --- changes ------------------------------------------------------------------------

    def wait(self, timeout):
        with self._cond:
            self._cond.wait(timeout)

    def changed(self):
        with self._cond:
            self.version += 1
            self._cond.notify_all()
        self.engine.send_now()

    def stop(self):
        self.reader.stop()
        self.engine.stop()
        if self.demo:
            self.demo.set()
        if self.server:
            self.server.stop()

    def host(self):
        cfg = self.config.get()
        return "0.0.0.0" if cfg.get("allow_network") else cfg["http_host"]

    def set_network(self, on):
        self.config.update({"allow_network": bool(on)})
        if self.server:
            self.server.restart(self.host())
        log("Other PCs on your network can now read the HUD" if on else "HUD is now only reachable from this PC")


def save_upload(folder, prefix, name, data_url):
    m = re.fullmatch(r"data:image/(png|jpeg|webp|svg\+xml);base64,(.+)", data_url or "", re.S)
    if not m:
        raise ValueError("send a PNG, JPG, WebP or SVG image")
    ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "svg+xml": ".svg"}[m.group(1)]
    stem = re.sub(r"[^A-Za-z0-9 _.-]+", "", os.path.splitext(os.path.basename(name or "logo"))[0]).strip() or "logo"
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, stem + ext), "wb") as f:
        f.write(base64.b64decode(m.group(2)))
    return prefix + stem + ext


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def _is_local(address):
    return address in ("127.0.0.1", "::1") or address.startswith("127.")


def _box(value):
    """A box from the control panel: [x0, y0, x1, y1] fractions, or None."""
    if not value:
        return None
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in value)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 0.005 or y1 - y0 < 0.005:
        raise ValueError("That box is too small: drag around the stock icons")
    return [round(v, 5) for v in (x0, y0, x1, y1)]


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, body, ctype="application/json", status=200):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(json.dumps(data), status=status)

        def _stream(self):
            """Server-sent events: the scoreboard, whenever it changes."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            last, sent = None, 0.0
            try:
                self.wfile.write(b"retry: 1000\n\n")
                while True:
                    key = (app.version, app.config.version)
                    now = time.monotonic()
                    if key != last or now - sent >= 5.0:
                        last, sent = key, now
                        self.wfile.write(b"data: " + json.dumps(app.scoreboard()).encode() + b"\n\n")
                        self.wfile.flush()
                    app.wait(1.0)
            except (ConnectionError, OSError):
                pass

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path in PAGES:
                target = PAGES[path]
                if target.startswith("/"):
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                path = "/" + target
            if path == "/api/state":
                return self._json(app.snapshot())
            if path == "/api/stream":
                return self._stream()
            if path == "/api/engine":
                return self._json(app.engine.status())
            if path == "/api/log":
                return self._json({"lines": since(int((parse_qs(url.query).get("after") or ["0"])[0]))})
            if path == "/api/reader/sources":
                return self._json(capture.sources())
            if path == "/api/reader/frame":
                fresh = (parse_qs(url.query).get("fresh") or ["1"])[0] != "0"
                jpeg = app.reader.snapshot_jpeg(fresh and not app.reader.running)
                if jpeg is None:
                    return self._json({"error": app.reader.error or "no picture"}, 404)
                return self._send(jpeg, "image/jpeg")
            full = web_file(unquote(path))
            if not full:
                return self._json({"error": "not found"}, 404)
            with open(full, "rb") as f:
                self._send(f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

        def do_POST(self):
            path = urlparse(self.path).path
            if not _is_local(self.client_address[0]):
                return self._json({"error": "settings can only be changed on the HUD's own PC"}, 403)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                side = body.get("side")
                if side is not None and side not in SIDES:
                    raise ValueError("side must be left or right")
                if path.startswith("/api/engine/"):
                    # Connect to GFX Engine (see enginelink.py).
                    reply = app.engine.handle("POST", path, body)
                    if reply is None:
                        return self._json({"error": "not found"}, 404)
                    return self._json(reply)
                if path == "/api/config":
                    body.pop("crews", None)    # (the score goes through /api/stocks)
                    app.config.update(body)
                    if "reader" in body:
                        app.reader.apply()
                    app.changed()
                elif path == "/api/stocks":
                    if body.get("reset"):
                        app.reset_stocks(body.get("total"))
                    elif "value" in body:
                        app.set_stocks(side, body["value"])
                    else:
                        app.lose(side, -int(body.get("delta") or 0))
                elif path == "/api/swap":
                    app.swap_sides()
                elif path == "/api/per-game":
                    app.config.update({"crews": {"per_game": max(1, min(5, int(body.get("value") or 3)))}})
                    app.reader.apply()
                    app.changed()
                elif path == "/api/wins":
                    t = app.config.get()[side]
                    need = (int(app.config.get()["best_of"]) + 1) // 2
                    app.config.update({side: {"wins": max(0, min(need, int(t["wins"] or 0) + int(body.get("delta") or 0)))}})
                    app.changed()
                elif path == "/api/player":
                    app.next_player(side, step=int(body.get("delta") or 1))
                elif path == "/api/reader":
                    app.config.update({"reader": {"on": bool(body.get("on"))}})
                    app.reader.apply()
                    app.changed()
                elif path == "/api/reader/box":
                    app.config.update({"reader": {"boxes": {side: _box(body.get("box"))}}})
                    app.reader.reset()
                    app.changed()
                elif path == "/api/notice/clear":
                    app.notice = None
                    app.changed()
                elif path == "/api/demo":
                    app.set_demo(bool(body.get("on")))
                elif path == "/api/network":
                    app.set_network(bool(body.get("on")))
                elif path == "/api/logo":
                    logo = save_upload(LOGO_DIR, "logos/", body.get("name"), body.get("data")) if body.get("data") else ""
                    app.config.update({"tournament_logo": logo})
                    app.changed()
                elif path == "/api/sponsor":
                    sponsor = save_upload(SPONSOR_DIR, "sponsors/", body.get("name"), body.get("data"))
                    app.config.update({"sponsors": app.config.get()["sponsors"] + [sponsor]})
                    app.changed()
                elif path == "/api/update/check":
                    app.updater.check()
                elif path == "/api/update/install":
                    app.updater.install_in_background()
                else:
                    return self._json({"error": "not found"}, 404)
                self._json(app.snapshot())
            except (ValueError, KeyError, TypeError, OSError) as e:
                self._json({"error": str(e)}, 400)

    return Handler


class Server:
    def __init__(self, app, host, port):
        self.app, self.port = app, port
        self._start(host)

    def _start(self, host):
        self.httpd = ThreadingHTTPServer((host, self.port), make_handler(self.app))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = host

    def restart(self, host):
        if host != self.host:
            self.httpd.shutdown()
            self.httpd.server_close()
            self._start(host)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def start(app):
    """Start the web server (raises OSError if the port is taken), and the reader if it's on."""
    port = app.config.get()["http_port"]
    app.server = Server(app, app.host(), port)
    app.reader.apply()
    clean_up_old_update()
    app.updater.check_in_background()
    log(f"Smash HUD {app.updater.current} running - control panel http://localhost:{port}/control")
    log(f"Scoreboard (browser source, 1920x1080): http://localhost:{port}/scoreboard")
    return app.server
