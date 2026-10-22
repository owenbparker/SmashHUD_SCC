"""The stock reader: watches the game's picture (capture.py), counts each
crew's stock icons (stocks.py) and turns that into the crews' score
(crews.py). Runs while "Read the game" is on."""

import io
import threading
import time

from . import capture, crews
from .log import log
from .stocks import StockRow

EVERY = 1 / 6     # frames a second it reads


class StockReader:
    def __init__(self, config, on_events):
        self.config = config
        self.on_events = on_events     # (list of crews events) -> None
        cfg = config.get()
        self.tracker = crews.CrewTracker(cfg["crews"]["per_game"])
        self.rows = {s: StockRow(cfg["crews"]["per_game"]) for s in crews.SIDES}
        self.source = None
        self.error = None
        self.frame = None              # the last picture (for drawing the boxes)
        self.frame_at = 0.0
        self.counts = {s: None for s in crews.SIDES}
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # --- on and off ---------------------------------------------------------------

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def apply(self):
        """Starts, stops or re-points the reader as the settings say."""
        cfg = self.config.get()
        spec = cfg["reader"]["source"]
        with self._lock:
            if self.source and self.source.spec != spec:
                self.source.close()
                self.source = None
            per_game = int(cfg["crews"]["per_game"] or 3)
            if per_game != self.tracker.per_game:
                self.tracker = crews.CrewTracker(per_game)
                self.rows = {s: StockRow(per_game) for s in crews.SIDES}
        if cfg["reader"]["on"] and not self.running:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            log("Reading the game's stocks")
        elif not cfg["reader"]["on"] and self.running:
            self._stop.set()
            self._thread.join(2)
            self._close()
            log("Stopped reading the game")

    def stop(self):
        self._stop.set()
        self._close()

    def _close(self):
        with self._lock:
            if self.source:
                self.source.close()
                self.source = None

    def reset(self):
        """Forget the game in progress (new crew battle)."""
        with self._lock:
            self.tracker.reset()
            for row in self.rows.values():
                row.forget()

    # --- reading ------------------------------------------------------------------

    def grab(self):
        """One picture from the source (also while the reader's off: for the boxes)."""
        with self._lock:
            if self.source is None:
                self.source = capture.Source(self.config.get()["reader"]["source"])
            source = self.source
        image, error = source.grab()
        self.error = error
        if image is not None:
            self.frame, self.frame_at = image, time.time()
        return image

    def snapshot_jpeg(self, fresh=True):
        from PIL import Image
        image = self.grab() if fresh or self.frame is None else self.frame
        if image is None:
            return None
        out = io.BytesIO()
        Image.fromarray(image).convert("RGB").save(out, "JPEG", quality=82)
        return out.getvalue()

    def read(self, image, now=None):
        """Counts the stocks in one picture and feeds the crews' tracker."""
        boxes = self.config.get()["reader"]["boxes"]
        with self._lock:
            counts = {s: self.rows[s].read(image, boxes.get(s)) for s in crews.SIDES}
            characters = {s: self.rows[s].character for s in crews.SIDES}
            events = self.tracker.feed(time.monotonic() if now is None else now, counts, characters)
        self.counts = counts
        if events:
            self.on_events(events)
        return events

    def _run(self):
        failed = None
        while not self._stop.is_set():
            started = time.monotonic()
            image = self.grab()
            if image is None:
                if self.error != failed:
                    log(f"Stock reader: {self.error}", "warn")
                failed = self.error
                self._stop.wait(1.0)
                continue
            if failed:
                log("Stock reader: getting the picture again")
            failed = None
            boxes = self.config.get()["reader"]["boxes"]
            if not (boxes.get("left") and boxes.get("right")):
                self.error = "draw the boxes around both crews' stocks (Stock reader page)"
            else:
                try:
                    self.read(image)
                except Exception as e:
                    self.error = f"couldn't read the stocks ({e!r})"
            self._stop.wait(max(0.0, EVERY - (time.monotonic() - started)))

    def info(self):
        cfg = self.config.get()
        return {"on": cfg["reader"]["on"], "running": self.running, "error": self.error, "counts": self.counts,
                "frame_age": round(time.time() - self.frame_at, 1) if self.frame_at else None,
                "game": self.tracker.info(), "opencv": _has_cv2()}


def _has_cv2():
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False
