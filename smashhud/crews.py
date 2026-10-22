"""Crew battles: each crew has one pool of stocks (12 by default) shared by
its players, and the score is how many it has left.

CrewTracker turns what the screen shows (how many stock icons each crew's
player has, and which character it is) into events:

    ("lost", side, n)   the crew lost n stocks
    ("out", side)       the crew's player was knocked out of the game (the
                        crew's next player comes in)
    ("check", text)     it couldn't tell who lost a game: the operator checks

Rules it follows, as crews are played:
- A count only counts once it's held for STEADY seconds (no flicker).
- A player who won the last game comes back with the stocks they had left;
  if the game starts them on more and they self-destruct down to that, those
  stocks aren't counted (they were lost last game already, or never had).
- When both rows are gone for GAME_OVER seconds the game's over. The loser
  is the side that went to 0 while the other still had stocks; else the one
  with fewer; else (both on 1) the side whose character changes next game.
"""

SIDES = ("left", "right")
STEADY = 0.8
GAME_OVER = 6.0


class CrewTracker:
    def __init__(self, per_game=3):
        self.per_game = per_game
        self.reset()

    def reset(self):
        self.seen = {s: (None, 0.0) for s in SIDES}     # raw count, since when
        self.steady = {s: None for s in SIDES}
        self.empty_since = None
        self.in_game = False
        self.expected = {s: self.per_game for s in SIDES}
        self.last = {s: 0 for s in SIDES}
        self.character = {s: None for s in SIDES}
        self.out = None
        self.carry = {s: None for s in SIDES}           # (character, stocks) the last game's winner kept
        self.tie = None

    def info(self):
        return {"in_game": self.in_game, "stocks": dict(self.last) if self.in_game else {s: None for s in SIDES},
                "expected": dict(self.expected), "unsure": bool(self.tie)}

    def feed(self, t, counts, characters):
        """One reading of the screen at time t: counts {side: stocks shown or
        None}, characters {side: id of the icon learnt}. -> list of events."""
        events = []
        raw = {s: counts.get(s) or 0 for s in SIDES}
        for s in SIDES:
            if raw[s] != self.seen[s][0]:
                self.seen[s] = (raw[s], t)
        if any(raw.values()):
            self.empty_since = None
        elif self.empty_since is None:
            self.empty_since = t

        for s in SIDES:
            value, since = self.seen[s]
            if t - since >= STEADY and value != self.steady[s]:
                before, self.steady[s] = self.steady[s], value
                if self.in_game:
                    events += self._changed(s, before, value, raw)

        if not self.in_game and all(self.steady[s] for s in SIDES):
            events += self._start(characters)
        elif self.in_game and self.empty_since is not None and t - self.empty_since >= GAME_OVER:
            events += self._end()
        return events

    def _start(self, characters):
        events = []
        if self.tie:
            changed = [s for s in SIDES if characters.get(s) != self.tie["character"][s]]
            if len(changed) == 1:
                loser = changed[0]
                winner = other(loser)
                events += self._lost(loser, self.tie["stocks"][loser]) + [("out", loser)]
                self.carry = {loser: None, winner: (self.tie["character"][winner], self.tie["stocks"][winner])}
            else:
                events.append(("check", "Couldn't tell who lost the last game (both were on 1 stock): check the score"))
            self.tie = None
        self.in_game = True
        self.out = None
        for s in SIDES:
            self.character[s] = characters.get(s)
            kept = self.carry[s]
            self.expected[s] = kept[1] if kept and kept[0] == self.character[s] else self.per_game
            self.last[s] = self.steady[s]
        return events

    def _changed(self, side, before, now, raw):
        if self.out or before is None or now >= self.last[side]:
            return []
        if now == 0 and not raw[other(side)]:
            return []    # (both rows gone at once: the game's over, not a KO)
        events = []
        lost = max(0, min(self.last[side], self.expected[side]) - now)
        self.last[side] = now
        if lost:
            events += self._lost(side, lost)
        if now == 0:
            self.out = side
            events.append(("out", side))
        return events

    def _lost(self, side, n):
        return [("lost", side, n)] if n > 0 else []

    def _end(self):
        events = []
        self.in_game = False
        loser = self.out
        if not loser and self.last["left"] != self.last["right"]:
            loser = min(SIDES, key=lambda s: self.last[s])
            events += self._lost(loser, min(self.last[loser], self.expected[loser])) + [("out", loser)]
        if loser:
            winner = other(loser)
            self.carry = {loser: None, winner: (self.character[winner], min(self.last[winner], self.expected[winner]))}
        else:
            self.tie = {"stocks": {s: min(self.last[s], self.expected[s]) for s in SIDES}, "character": dict(self.character)}
            self.carry = {s: None for s in SIDES}
        for s in SIDES:
            self.steady[s] = None
        return events


def other(side):
    return "right" if side == "left" else "left"
