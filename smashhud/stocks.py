"""Counting a player's stock icons on the game screen.

Smash Ultimate shows each player's stocks as a row of small, identical icons
of their character under their damage percent. The operator draws a box
around each crew's row once, while the player has full stocks (3 icons for
3-stock games): the box is cut into that many slots, one icon each.

The icons are learnt from the screen itself, so any character, colour and
stage works: when two slots look the same as each other (and aren't flat
colour), that's the player's icon. After that, the stocks are the places
along the row that still look like that icon. A new character (the crew's next player) is a new
icon, learnt the same way at the start of their game.
"""

import numpy as np

SLOT = 24          # each slot is compared at this size (pixels)
TEXTURE = 9.0      # a slot with less detail than this (std of its edges) is empty
COLOUR = 45.0      # slots whose average colours differ more than this aren't the same icon
SAME = 0.80        # two slots this alike (correlation) show the same icon
HOLDS = 0.62       # a slot this like the icon holds a stock
NEW_CHARACTER = 0.55   # a learnt icon this unlike the last one is another character


def strip(image, box, count):
    """The box (fractions of the frame: [x0, y0, x1, y1]) of an RGB frame
    (numpy array, height x width x 3), scaled to `count` slots side by side,
    each SLOT x SLOT, or None if it's too small."""
    from PIL import Image
    h, w = image.shape[:2]
    x0, y0, x1, y1 = (int(round(v * s)) for v, s in zip(box, (w, h, w, h)))
    x0, x1 = sorted((max(0, min(w - 1, x0)), max(1, min(w, x1))))
    y0, y1 = sorted((max(0, min(h - 1, y0)), max(1, min(h, y1))))
    if x1 - x0 < count * 2 or y1 - y0 < 2:
        return None
    crop = Image.fromarray(np.ascontiguousarray(image[y0:y1, x0:x1, :3])).resize((SLOT * count, SLOT), Image.BILINEAR)
    return np.asarray(crop, dtype=np.float32)


def slots(row, count):
    return [row[:, i * SLOT:(i + 1) * SLOT] for i in range(count)]


def matches(row, icon):
    """Where the icon is in the row: the left edges of the places it matches,
    a whole icon apart (so the count is right however the game lines the
    icons up as stocks go)."""
    from numpy.lib.stride_tricks import sliding_window_view
    d = detail(row)
    parts = sliding_window_view(d, (SLOT, SLOT, 3))[0, :, 0]            # (positions, SLOT, SLOT, 3)
    colours = sliding_window_view(row, (SLOT, SLOT, 3))[0, :, 0].mean((1, 2))
    parts = parts.reshape(len(parts), -1)
    spread = parts.std(1)
    parts = parts - parts.mean(1, keepdims=True)
    ref = detail(icon).reshape(-1)
    ref = ref - ref.mean()
    norm = np.sqrt((parts * parts).sum(1) * (ref * ref).sum())
    score = np.where(norm > 0, (parts @ ref) / np.maximum(norm, 1e-6), 0.0)
    same_colour = np.abs(colours - icon.mean((0, 1))).max(1) <= COLOUR
    score = np.where((spread >= TEXTURE) & same_colour, score, -1.0)
    picked = []
    for x in np.argsort(-score):
        if score[x] < HOLDS:
            break
        if all(abs(int(x) - p) >= SLOT * 0.7 for p in picked):
            picked.append(int(x))
    return sorted(picked)


def _blur(img):
    """A 5x5 box blur (edges repeated)."""
    p = np.pad(img, ((2, 2), (2, 2), (0, 0)), mode="edge")
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0), (0, 0)))
    h, w = img.shape[:2]
    return (c[5:5 + h, 5:5 + w] - c[:h, 5:5 + w] - c[5:5 + h, :w] + c[:h, :w]) / 25.0


def detail(img):
    """The picture's fine detail (edges, outlines) without its broad shading:
    a stage's smooth gradients don't look like icons."""
    return img - _blur(img)


def texture(slot):
    return float(detail(slot).std())


def alike(a, b):
    """How alike two slots are (-1..1): the correlation of their detail, and
    nothing if their colours differ a lot."""
    if float(np.abs(a.mean((0, 1)) - b.mean((0, 1))).max()) > COLOUR:
        return 0.0
    a = detail(a)
    b = detail(b)
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d else 0.0


class StockRow:
    """One crew's row of stock icons: read() -> how many stocks it shows now
    (0 while there's none), or None until the player's icon has been seen."""

    def __init__(self, count=3):
        self.count = count
        self.icon = None
        self.character = 0   # goes up each time another character's icon is learnt

    def learn(self, cut):
        """The player's icon, if two slots show the same thing: their average."""
        busy = [s for s in cut if texture(s) >= TEXTURE]
        for i in range(len(busy)):
            same = [busy[i]] + [b for b in busy[i + 1:] if alike(busy[i], b) >= SAME]
            if len(same) >= 2:
                return np.mean(same, axis=0)
        return None

    def read(self, image, box):
        if not box:
            return None
        row = strip(image, box, self.count)
        if row is None:
            return None
        icon = self.learn(slots(row, self.count))
        if icon is not None and (self.icon is None or alike(icon, self.icon) < NEW_CHARACTER):
            self.character += 1
            self.icon = icon
        if self.icon is None:
            return None
        return min(self.count, len(matches(row, self.icon)))

    def forget(self):
        self.icon = None
