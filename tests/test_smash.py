import json
import os
import sys
import tempfile
import unittest
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smashhud.crews import CrewTracker  # noqa: E402
from smashhud.stocks import StockRow  # noqa: E402

W, H = 1920, 1080
ICON = 34
LEFT = (560, 1000)    # where each crew's first stock icon is drawn (x, y)
RIGHT = (1180, 1000)


def icon(seed):
    """A made-up character head: blobs of colour with a dark outline."""
    rng = np.random.default_rng(seed)
    img = np.zeros((ICON, ICON, 3), np.float32)
    yy, xx = np.mgrid[:ICON, :ICON]
    for _ in range(6):
        cx, cy, r = rng.integers(6, ICON - 6, 2).tolist() + [int(rng.integers(4, 10))]
        img[(xx - cx) ** 2 + (yy - cy) ** 2 < r * r] = rng.integers(40, 255, 3)
    edge = ((xx - ICON / 2) ** 2 + (yy - ICON / 2) ** 2)
    img[(edge > (ICON / 2 - 2) ** 2)] = 10
    return img


def frame(left=3, right=3, chars=(1, 2), seed=0, centred=False):
    """A game screen: a busy stage, and each crew's stock icons under their %."""
    rng = np.random.default_rng(100 + seed)
    yy, xx = np.mgrid[:H, :W]
    img = np.stack([(xx * 0.1 + seed * 7) % 255, (yy * 0.15) % 255, ((xx + yy) * 0.05) % 255], -1).astype(np.float32)
    img += rng.normal(0, 6, img.shape)
    for (x, y), n, ch in ((LEFT, left, chars[0]), (RIGHT, right, chars[1])):
        pic = icon(ch)
        start = x + (3 - n) * (ICON + 4) // 2 if centred else x
        for i in range(n):
            img[y:y + ICON, start + i * (ICON + 4):start + i * (ICON + 4) + ICON] = pic
    return np.clip(img, 0, 255).astype(np.uint8)


def box(at):
    x, y = at
    return [(x - 2) / W, (y - 2) / H, (x + 3 * (ICON + 4) - 2) / W, (y + ICON + 2) / H]


BOXES = {"left": box(LEFT), "right": box(RIGHT)}


class StockRowTest(unittest.TestCase):
    def test_counts_icons_as_they_go(self):
        row = StockRow(3)
        self.assertEqual(row.read(frame(3, 3), BOXES["left"]), 3)
        for n in (2, 1, 0):
            self.assertEqual(row.read(frame(n, 3, seed=n), BOXES["left"]), n)

    def test_icons_lined_up_in_the_middle(self):
        row = StockRow(3)
        row.read(frame(3, 3), BOXES["left"])
        self.assertEqual(row.read(frame(2, 3, centred=True), BOXES["left"]), 2)
        self.assertEqual(row.read(frame(1, 3, centred=True), BOXES["left"]), 1)

    def test_nothing_until_an_icon_is_seen(self):
        row = StockRow(3)
        self.assertIsNone(row.read(frame(0, 0), BOXES["left"]))
        self.assertIsNone(row.read(frame(3, 3), None))

    def test_a_new_character_is_learnt(self):
        row = StockRow(3)
        row.read(frame(3, 3, chars=(1, 2)), BOXES["left"])
        first = row.character
        self.assertEqual(row.read(frame(3, 3, chars=(7, 2)), BOXES["left"]), 3)
        self.assertEqual(row.character, first + 1)
        self.assertEqual(row.read(frame(2, 3, chars=(7, 2)), BOXES["left"]), 2)


class Feeder:
    """Feeds a CrewTracker readings over time; collects its events."""

    def __init__(self, per_game=3):
        self.t = 0.0
        self.tracker = CrewTracker(per_game)
        self.events = []

    def hold(self, left, right, seconds=1.0, chars=(1, 2)):
        end = self.t + seconds
        while self.t < end:
            self.events += self.tracker.feed(self.t, {"left": left, "right": right}, {"left": chars[0], "right": chars[1]})
            self.t += 0.2
        return self

    def lost(self):
        out = {"left": 0, "right": 0}
        for e in self.events:
            if e[0] == "lost":
                out[e[1]] += e[2]
        return out

    def outs(self):
        return [e[1] for e in self.events if e[0] == "out"]


class CrewTrackerTest(unittest.TestCase):
    def test_stocks_lost_in_a_game(self):
        f = Feeder().hold(3, 3).hold(2, 3).hold(2, 2).hold(1, 2)
        self.assertEqual(f.lost(), {"left": 2, "right": 1})

    def test_flicker_isnt_counted(self):
        f = Feeder().hold(3, 3).hold(2, 3, 0.4).hold(3, 3).hold(2, 3, 0.2).hold(3, 3)
        self.assertEqual(f.lost(), {"left": 0, "right": 0})

    def test_knocked_out_then_next_game_with_carried_stocks(self):
        f = Feeder().hold(3, 3).hold(2, 3).hold(1, 3).hold(1, 2).hold(0, 2)
        self.assertEqual(f.lost(), {"left": 3, "right": 1})
        self.assertEqual(f.outs(), ["left"])
        f.hold(0, 0, 7)   # results screen
        # Next game: a new left player; the right player starts on 3 and SDs down to the 2 they kept.
        f.hold(3, 3, chars=(5, 2)).hold(3, 2, chars=(5, 2)).hold(2, 2, chars=(5, 2)).hold(2, 1, chars=(5, 2))
        self.assertEqual(f.lost(), {"left": 4, "right": 2})

    def test_game_over_screen_isnt_a_ko_for_both(self):
        # The last KO isn't seen before GAME!: both rows go at once. Fewer stocks lost.
        f = Feeder().hold(3, 3).hold(1, 3).hold(1, 2).hold(0, 0, 7)
        self.assertEqual(f.lost(), {"left": 3, "right": 1})
        self.assertEqual(f.outs(), ["left"])

    def test_tie_is_settled_by_who_changes_character(self):
        f = Feeder().hold(3, 3).hold(1, 3).hold(1, 1).hold(0, 0, 7)
        self.assertEqual(f.lost(), {"left": 2, "right": 2})
        f.hold(3, 3, chars=(1, 9))   # the right crew sent someone new: they lost
        self.assertEqual(f.lost(), {"left": 2, "right": 3})
        self.assertEqual(f.outs(), ["right"])
        # The left player kept 1 stock: SDs from 3 to 1 aren't counted.
        f.hold(2, 3, chars=(1, 9)).hold(1, 3, chars=(1, 9))
        self.assertEqual(f.lost(), {"left": 2, "right": 3})

    def test_a_short_gap_isnt_the_end_of_the_game(self):
        f = Feeder().hold(3, 3).hold(0, 0, 3).hold(3, 3)
        self.assertEqual(f.lost(), {"left": 0, "right": 0})
        self.assertTrue(f.tracker.in_game)


class AppTest(unittest.TestCase):
    """The HUD's web server: pages, the score and the events from the reader."""

    @classmethod
    def setUpClass(cls):
        from smashhud.app import HudApp, start
        from smashhud.config import Config
        cls.tmp = tempfile.mkdtemp()
        config = Config(os.path.join(cls.tmp, "config.json"))
        config.update({"http_port": 8792})
        cls.app = HudApp(config)
        cls.app.updater.check_in_background = lambda: None
        start(cls.app)
        cls.base = "http://127.0.0.1:8792"

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.read()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_pages(self):
        for path in ("/control", "/scoreboard", "/shell.js", "/enginelink.js", "/fonts/Anton-Regular.ttf"):
            self.assertTrue(self.get(path))

    def test_score_by_hand_and_from_the_reader(self):
        self.post("/api/config", {"left": {"players": ["Alpha", "Bravo", "Charlie"]}})
        s = self.post("/api/stocks", {"reset": True, "total": 12})
        self.assertEqual((s["scoreboard"]["left"]["stocks"], s["scoreboard"]["left"]["name"]), (12, "Alpha"))
        s = self.post("/api/stocks", {"side": "right", "delta": -1})
        self.assertEqual(s["scoreboard"]["right"]["stocks"], 11)
        self.app.on_reader([("lost", "left", 3), ("out", "left")])
        sb = self.app.scoreboard()
        self.assertEqual((sb["left"]["stocks"], sb["left"]["name"], sb["right"]["name"]), (9, "Bravo", "PLAYER 2"))
        self.app.on_reader([("lost", "left", 20)])
        self.assertEqual(self.app.scoreboard()["left"]["stocks"], 0)

    def test_reader_box(self):
        s = self.post("/api/reader/box", {"side": "left", "box": [0.5, 0.9, 0.3, 0.95]})
        self.assertEqual(s["config"]["reader"]["boxes"]["left"], [0.3, 0.9, 0.5, 0.95])

    def test_engine_snapshot(self):
        snap = self.app._engine_snapshot()
        self.assertIn("left_stocks", snap["fields"])
        self.assertEqual(snap["hud"]["graphics"][0]["path"], "/scoreboard")

    def test_reader_reads_frames(self):
        from smashhud.reader import StockReader
        self.app.config.update({"reader": {"boxes": BOXES}})
        got = []
        r = StockReader(self.app.config, got.extend)
        t = 0.0
        for n in (3, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2):
            r.read(frame(n, 3, seed=int(t * 10)), now=t)
            t += 0.2
        self.assertEqual(got, [("lost", "left", 1)])


if __name__ == "__main__":
    unittest.main()
