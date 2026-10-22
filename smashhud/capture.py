"""Where the game's picture comes from (Windows): a capture card or virtual
camera (OBS Virtual Camera, vMix's External output), a window (a projector
or multiview), or a whole screen. Each gives RGB frames as numpy arrays.

A capture card can usually only be opened by one program: if vMix or OBS
already uses it, point the reader at their virtual camera or a projector
window instead.
"""

import sys
import threading

import numpy as np

from .log import log


def sources():
    """What can be read: {"devices": [{index, label}], "windows": [titles], "screens": [labels]}."""
    return {"devices": devices(), "windows": windows(), "screens": screens()}


def devices(most=6):
    """Capture devices by number (Windows doesn't give OpenCV their names):
    the ones that open and give a picture."""
    try:
        import cv2
    except ImportError:
        return []
    out = []
    for i in range(most):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(i)
        try:
            if cap.isOpened():
                w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                out.append({"index": i, "label": f"Capture device {i + 1} ({w}x{h})"})
        finally:
            cap.release()
    return out


def windows():
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value.strip()
            if title and title not in found and title not in ("Program Manager",):
                found.append(title)
        return True

    user32.EnumWindows(each, 0)
    return found


def monitors():
    """Each screen's (x, y, width, height), the main screen first."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    out = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def each(hmon, _dc, _rect, _):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        entry = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        if info.dwFlags & 1:
            out.insert(0, entry)
        else:
            out.append(entry)
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, each, 0)
    return out


def screens():
    return [f"Screen {i + 1} ({w}x{h})" for i, (_x, _y, w, h) in enumerate(monitors())]


class Source:
    """Frames from one source: grab() -> (RGB numpy array, None) or (None, reason)."""

    def __init__(self, spec):
        self.spec = dict(spec or {})
        self.kind = self.spec.get("kind") or "device"
        self._cap = None
        self._lock = threading.Lock()

    def grab(self):
        with self._lock:
            try:
                if self.kind == "device":
                    return self._device()
                if self.kind == "window":
                    return self._window()
                if self.kind == "screen":
                    return self._screen()
                return None, f"unknown source {self.kind!r}"
            except Exception as e:   # (a source that goes away can't stop the reader)
                return None, f"couldn't read the picture ({e})"

    def close(self):
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    def _device(self):
        try:
            import cv2
        except ImportError:
            return None, "capture devices need OpenCV (pip install opencv-python-headless)"
        index = int(self.spec.get("index") or 0)
        if self._cap is None:
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
            # Ask for 1080p; the device gives what it can.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            if not cap.isOpened():
                cap.release()
                return None, (f"capture device {index + 1} won't open (is vMix or OBS using it? Use their virtual "
                              "camera or a projector window instead)")
            self._cap = cap
            log(f"Reading capture device {index + 1}")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._cap.release()
            self._cap = None
            return None, f"capture device {index + 1} gave no picture"
        return frame[:, :, ::-1], None   # (BGR -> RGB)

    def _window(self):
        title = (self.spec.get("window") or "").strip()
        if not title:
            return None, "pick a window to read"
        if sys.platform != "win32":
            return None, "windows can only be read on Windows"
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None, f"window \"{title}\" not found"
        if user32.IsIconic(hwnd):
            return None, f"\"{title}\" is minimised"
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        width, height = rect.right, rect.bottom
        if width < 200 or height < 120:
            return None, f"\"{title}\" is too small"
        image = print_window(hwnd, width, height)
        if image is None:
            point = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(point))
            from PIL import ImageGrab
            image = ImageGrab.grab((point.x, point.y, point.x + width, point.y + height), all_screens=True)
        return np.asarray(image.convert("RGB")), None

    def _screen(self):
        mons = monitors()
        if not mons:
            return None, "no screens found"
        i = int(self.spec.get("screen") or 0)
        x, y, w, h = mons[i] if i < len(mons) else mons[0]
        from PIL import ImageGrab
        return np.asarray(ImageGrab.grab((x, y, x + w, y + h), all_screens=True).convert("RGB")), None


def print_window(hwnd, width, height):
    """The window's client area as Windows composes it, even when other
    windows are over it, or None (it came back black: no picture)."""
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]

    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight = width, -height   # top-down rows
    info.biPlanes, info.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    dc = gdi32.CreateCompatibleDC(None)
    bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    if not bitmap:
        gdi32.DeleteDC(dc)
        return None
    old = gdi32.SelectObject(dc, bitmap)
    try:
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT: GPU-drawn windows (vMix, OBS) come out too.
        if not user32.PrintWindow(hwnd, dc, 1 | 2):
            return None
        data = ctypes.string_at(bits, width * height * 4)
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
    image = Image.frombuffer("RGBX", (width, height), data, "raw", "BGRX", 0, 1).convert("RGB")
    if max(image.resize((64, 36)).convert("L").getextrema()) < 8:
        return None
    return image
