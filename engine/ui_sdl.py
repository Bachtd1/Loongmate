"""
Giao diện thật của LoongMate vẽ bằng SDL2 qua ctypes (stdlib-only).

Lý do: pak khi Leaf khởi chạy từ tab Apps KHÔNG có terminal, nên tui.py (in ra
stdout) không hiện gì. Máy không có trình biên dịch nên không build app C/C++.
File này lo phần SDL (ctypes, vẽ chữ, vòng lặp sự kiện, chế độ chụp màn hình);
các màn hình nằm ở engine/ui_screens.py.

KHÔNG BAO GIỜ bịa số liệu: mọi con số phải đến từ engine thật.

Chạy trên thiết bị:   python ui_sdl.py
Chụp màn hình (test): python ui_sdl.py --screenshot /tmp/x.ppm --script "DOWN,A"
"""

import os
import sys
import time
import ctypes
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.probe import PlatformProbe
from engine.inventory import InventoryEngine, find_systems_catalog
from engine.doctor import LibraryDoctor
from engine.savevault import SaveVault
from engine.health import DeviceHealth
from engine.ui_screens import App, WIN_W, WIN_H, AXIS_THRESHOLD

# Hằng số SDL2 (đặc tả đã kiểm chứng). SDL_CONTROLLERBUTTONDOWN là 0x651; 0x653 là
# SDL_CONTROLLERDEVICEADDED (đã gây "nút controller 127" lạ trên máy thật).
SDL_INIT_VIDEO = 0x20
SDL_INIT_JOYSTICK = 0x200
SDL_INIT_GAMECONTROLLER = 0x2000
SDL_QUIT = 0x100
SDL_KEYDOWN = 0x300
SDL_JOYAXISMOTION = 0x600
SDL_JOYHATMOTION = 0x602
SDL_JOYBUTTONDOWN = 0x603
SDL_CONTROLLERBUTTONDOWN = 0x651
SDL_WINDOW_FULLSCREEN_DESKTOP = 0x1001
SDL_WINDOW_SHOWN = 0x4
SDL_RENDERER_ACCELERATED = 0x2
SDL_RENDERER_SOFTWARE = 0x1
SDL_PIXELFORMAT_ARGB8888 = 0x16362004


class SDL_Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32), ("w", ctypes.c_int32), ("h", ctypes.c_int32)]


class SDL_Color(ctypes.Structure):
    _fields_ = [("r", ctypes.c_uint8), ("g", ctypes.c_uint8), ("b", ctypes.c_uint8), ("a", ctypes.c_uint8)]


class SDL_Keysym(ctypes.Structure):
    _fields_ = [("scancode", ctypes.c_int32), ("sym", ctypes.c_int32),
                ("mod", ctypes.c_uint16), ("unused", ctypes.c_uint32)]


class SDL_KeyboardEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32), ("windowID", ctypes.c_uint32),
                ("state", ctypes.c_uint8), ("repeat", ctypes.c_uint8), ("p2", ctypes.c_uint8), ("p3", ctypes.c_uint8),
                ("keysym", SDL_Keysym)]


class SDL_JoyButtonEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32), ("which", ctypes.c_int32),
                ("button", ctypes.c_uint8), ("state", ctypes.c_uint8), ("p1", ctypes.c_uint8), ("p2", ctypes.c_uint8)]


class SDL_JoyHatEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32), ("which", ctypes.c_int32),
                ("hat", ctypes.c_uint8), ("value", ctypes.c_uint8), ("p1", ctypes.c_uint8), ("p2", ctypes.c_uint8)]


class SDL_JoyAxisEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32), ("which", ctypes.c_int32),
                ("axis", ctypes.c_uint8), ("p1", ctypes.c_uint8), ("p2", ctypes.c_uint8), ("p3", ctypes.c_uint8),
                ("value", ctypes.c_int16), ("p4", ctypes.c_uint16)]


class SDL_ControllerButtonEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("timestamp", ctypes.c_uint32), ("which", ctypes.c_int32),
                ("button", ctypes.c_uint8), ("state", ctypes.c_uint8), ("p1", ctypes.c_uint8), ("p2", ctypes.c_uint8)]


class SDL_Event(ctypes.Union):
    _fields_ = [("type", ctypes.c_uint32), ("key", SDL_KeyboardEvent), ("jbutton", SDL_JoyButtonEvent),
                ("jhat", SDL_JoyHatEvent), ("jaxis", SDL_JoyAxisEvent), ("cbutton", SDL_ControllerButtonEvent),
                ("padding", ctypes.c_ubyte * 56)]


def _load_lib(names: List[str]):
    last_err = None
    for n in names:
        try:
            return ctypes.CDLL(n)
        except OSError as e:
            last_err = e
    raise OSError(f"Không tải được thư viện nào trong {names}: {last_err}")


def load_sdl():
    """Nạp SDL2/SDL2_ttf và gán đúng argtypes/restype cho các hàm trả về con
    trỏ — thiếu bước này thì trên ARM64 ctypes mặc định cắt con trỏ về 32-bit
    (c_int), gây lỗi khó hiểu hoặc crash."""
    sdl = _load_lib(["libSDL2-2.0.so.0", "libSDL2-2.0.so", "libSDL2.so", "SDL2.dll"])
    ttf = _load_lib(["libSDL2_ttf-2.0.so.0", "libSDL2_ttf.so", "SDL2_ttf.dll"])

    sdl.SDL_GetError.restype = ctypes.c_char_p
    sdl.SDL_CreateWindow.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
    sdl.SDL_CreateWindow.restype = ctypes.c_void_p
    sdl.SDL_CreateRenderer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    sdl.SDL_CreateRenderer.restype = ctypes.c_void_p
    sdl.SDL_CreateTextureFromSurface.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sdl.SDL_CreateTextureFromSurface.restype = ctypes.c_void_p
    sdl.SDL_JoystickOpen.argtypes = [ctypes.c_int]
    sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
    sdl.SDL_GameControllerOpen.argtypes = [ctypes.c_int]
    sdl.SDL_GameControllerOpen.restype = ctypes.c_void_p
    sdl.SDL_RenderCopy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(SDL_Rect), ctypes.POINTER(SDL_Rect)]
    sdl.SDL_RenderCopy.restype = ctypes.c_int
    sdl.SDL_QueryTexture.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                                      ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    sdl.SDL_QueryTexture.restype = ctypes.c_int
    sdl.SDL_RenderReadPixels.argtypes = [ctypes.c_void_p, ctypes.POINTER(SDL_Rect), ctypes.c_uint32,
                                          ctypes.c_void_p, ctypes.c_int]
    sdl.SDL_RenderReadPixels.restype = ctypes.c_int
    sdl.SDL_RenderFillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(SDL_Rect)]
    sdl.SDL_RenderDrawRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(SDL_Rect)]
    sdl.SDL_PollEvent.argtypes = [ctypes.POINTER(SDL_Event)]
    # Mọi hàm nhận con trỏ phải có argtypes, nếu không ctypes cắt con trỏ 64-bit
    # về int 32-bit và SDL segfault (đã gặp trên máy thật ở SDL_SetRenderDrawColor).
    vp = ctypes.c_void_p
    u8 = ctypes.c_uint8
    sdl.SDL_SetRenderDrawColor.argtypes = [vp, u8, u8, u8, u8]
    for fn in ("SDL_RenderClear", "SDL_RenderPresent", "SDL_DestroyTexture", "SDL_FreeSurface",
               "SDL_DestroyRenderer", "SDL_DestroyWindow", "SDL_JoystickClose", "SDL_GameControllerClose"):
        getattr(sdl, fn).argtypes = [vp]
    ttf.TTF_CloseFont.argtypes = [vp]

    ttf.TTF_OpenFont.argtypes = [ctypes.c_char_p, ctypes.c_int]
    ttf.TTF_OpenFont.restype = ctypes.c_void_p
    ttf.TTF_RenderUTF8_Blended.argtypes = [ctypes.c_void_p, ctypes.c_char_p, SDL_Color]
    ttf.TTF_RenderUTF8_Blended.restype = ctypes.c_void_p
    ttf.TTF_SizeUTF8.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    ttf.TTF_SizeUTF8.restype = ctypes.c_int
    return sdl, ttf


def find_font() -> Optional[str]:
    """Thử các đường dẫn font đã xác nhận có trên máy thật, ưu tiên Nunito-Bold
    (có dấu tiếng Việt), rồi Inter, rồi IBMPlexSans; thử cả $UMRK_LAUNCHER_PATH
    lẫn $SDCARD_PATH/.system/leaf/platforms/mlp1/launcher làm gốc."""
    bases = []
    launcher = os.environ.get("UMRK_LAUNCHER_PATH")
    if launcher:
        bases.append(os.path.join(launcher, "res", "fonts"))
    sdcard = os.environ.get("SDCARD_PATH")
    if sdcard:
        bases.append(os.path.join(sdcard, ".system", "leaf", "platforms", "mlp1", "launcher", "res", "fonts"))
    for fam, fname in (("Nunito", "Nunito-Bold.ttf"), ("Inter", "Inter.ttf"), ("IBMPlexSans", "IBMPlexSans-Bold.ttf")):
        for base in bases:
            p = os.path.join(base, fam, fname)
            if os.path.isfile(p):
                return p
    return None


def write_ppm(sdl, renderer, path: str, w: int, h: int) -> None:
    """Đọc pixel từ renderer (ARGB8888) và ghi PPM P6 (RGB). Thứ tự byte trong
    bộ nhớ của ARGB8888 little-endian là B,G,R,A — đã kiểm bằng mắt khi dựng
    PNG từ file PPM này (xem báo cáo)."""
    pitch = w * 4
    buf = ctypes.create_string_buffer(h * pitch)
    ret = sdl.SDL_RenderReadPixels(renderer, None, SDL_PIXELFORMAT_ARGB8888, buf, pitch)
    if ret != 0:
        raise RuntimeError(f"SDL_RenderReadPixels lỗi: {sdl.SDL_GetError().decode('utf-8', 'replace')}")
    raw = buf.raw
    out = bytearray(w * h * 3)
    oi = 0
    for i in range(0, len(raw), 4):
        out[oi] = raw[i + 2]      # R
        out[oi + 1] = raw[i + 1]  # G
        out[oi + 2] = raw[i]      # B
        oi += 3
    with open(path, "wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode("ascii"))
        f.write(bytes(out))


class TextCache:
    """Cache texture theo (text, size, color). Giới hạn 256 mục (máy chỉ có
    1GB RAM) với đào thải FIFO; SDL_DestroyTexture khi đào thải/dọn."""

    def __init__(self, sdl, ttf, renderer, fonts: Dict[str, Any], cap: int = 256):
        self.sdl, self.ttf, self.renderer, self.fonts = sdl, ttf, renderer, fonts
        self.cap = cap
        self.cache: Dict[Tuple[str, str, Tuple[int, int, int]], Tuple[Any, int, int]] = {}
        self.order: List[Tuple[str, str, Tuple[int, int, int]]] = []

    def get(self, text: str, size_key: str, color: Tuple[int, int, int]):
        key = (text, size_key, color)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        font = self.fonts[size_key]
        sdl_color = SDL_Color(color[0], color[1], color[2], 255)
        surface = self.ttf.TTF_RenderUTF8_Blended(font, text.encode("utf-8"), sdl_color)
        if not surface:
            raise RuntimeError(f"TTF_RenderUTF8_Blended lỗi: {self.sdl.SDL_GetError().decode('utf-8', 'replace')}")
        texture = self.sdl.SDL_CreateTextureFromSurface(self.renderer, surface)
        self.sdl.SDL_FreeSurface(surface)
        w, h = ctypes.c_int(), ctypes.c_int()
        self.sdl.SDL_QueryTexture(texture, None, None, ctypes.byref(w), ctypes.byref(h))
        if len(self.cache) >= self.cap:
            old_key = self.order.pop(0)
            old_tex, _, _ = self.cache.pop(old_key)
            self.sdl.SDL_DestroyTexture(old_tex)
        value = (texture, w.value, h.value)
        self.cache[key] = value
        self.order.append(key)
        return value

    def clear(self):
        for tex, _, _ in self.cache.values():
            self.sdl.SDL_DestroyTexture(tex)
        self.cache.clear()
        self.order.clear()



def wrap_text(g, size: str, text: str, max_width: int) -> List[str]:
    """Xuống dòng theo từ; từ dài hơn khung (vd đường dẫn) thì cắt theo ký tự."""
    lines: List[str] = []
    cur = ""
    for word in text.split(" "):
        trial = (cur + " " + word) if cur else word
        if g.width(trial, size) <= max_width:
            cur = trial
            continue
        prefix = (cur + " ") if cur else ""
        if g.width(word, size) <= max_width:
            lines.append(cur)
            cur = word
            continue
        while g.width(prefix + word, size) > max_width and len(word) > 1:
            k = len(word)
            while k > 1 and g.width(prefix + word[:k], size) > max_width:
                k -= 1
            lines.append(prefix + word[:k])
            word, prefix = word[k:], ""
        cur = prefix + word
    if cur:
        lines.append(cur)
    return lines or [""]


class Gfx:
    """Lớp vẽ mỏng trên SDL cho ui_screens (mọi lệnh SDL chỉ gọi từ thread chính)."""

    def __init__(self, sdl, ttf, renderer, fonts):
        self.sdl, self.ttf, self.renderer, self.fonts = sdl, ttf, renderer, fonts
        self.tc = TextCache(sdl, ttf, renderer, fonts)

    def clear(self, color):
        self.sdl.SDL_SetRenderDrawColor(self.renderer, *color, 255)
        self.sdl.SDL_RenderClear(self.renderer)

    def text(self, s: str, size: str, color, x: int, y: int):
        if not s:
            return 0, 0
        tex, w, h = self.tc.get(s, size, color)
        dst = SDL_Rect(x, y, w, h)
        self.sdl.SDL_RenderCopy(self.renderer, tex, None, ctypes.byref(dst))
        return w, h

    def fill(self, x, y, w, h, color):
        r = SDL_Rect(x, y, w, h)
        self.sdl.SDL_SetRenderDrawColor(self.renderer, *color, 255)
        self.sdl.SDL_RenderFillRect(self.renderer, ctypes.byref(r))

    def outline(self, x, y, w, h, color, t):
        self.sdl.SDL_SetRenderDrawColor(self.renderer, *color, 255)
        for i in range(t):
            r = SDL_Rect(x + i, y + i, w - 2 * i, h - 2 * i)
            self.sdl.SDL_RenderDrawRect(self.renderer, ctypes.byref(r))

    def width(self, s: str, size: str) -> int:
        w, h = ctypes.c_int(), ctypes.c_int()
        self.ttf.TTF_SizeUTF8(self.fonts[size], s.encode("utf-8"), ctypes.byref(w), ctypes.byref(h))
        return w.value

    def wrap(self, s: str, size: str, maxw: int) -> List[str]:
        return wrap_text(self, size, s, maxw)

    def fit(self, s: str, size: str, maxw: int) -> str:
        if self.width(s, size) <= maxw:
            return s
        while s and self.width(s + "…", size) > maxw:
            s = s[:-1]
        return s + "…"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Giao diện SDL2 thật của LoongMate (vẽ lên màn hình, không cần terminal).")
    p.add_argument("--primary", help="Đường dẫn thẻ SD chính")
    p.add_argument("--secondary", help="Đường dẫn thẻ SD phụ")
    p.add_argument("--userdata", help="Đường dẫn userdata của LoongMate")
    p.add_argument("--screenshot", help="Chế độ headless: chạy --script rồi ghi 1 khung hình ra file PPM và thoát "
                                         "(không joystick, không event loop)")
    p.add_argument("--screen", choices=["home", "device", "cards", "savevault", "library", "battery", "update", "hottabs"], default="home",
                   help="Mở thẳng một màn khi khởi động (bản native dùng khi bấm A trên một thẻ)")
    p.add_argument("--apps-dir", help="Thư mục chứa các pak khi cập nhật (mặc định <thẻ chính>/Apps/mlp1; dùng sandbox để thử)")
    p.add_argument("--script", default="", help="Chuỗi action phân tách bằng dấu phẩy chạy trước khi chụp, vd 'DOWN,A,A'. "
                                                 "Action: UP DOWN LEFT RIGHT A B X Y, BTN<n> (nút joystick), HAT<n>; tiền tố ~ = không chờ tác vụ nền")
    return p


def run_script(app: App, script: str) -> None:
    for tok in [t.strip().upper() for t in script.split(",") if t.strip()]:
        nowait = tok.startswith("~")      # "~A": chạy rồi KHÔNG chờ tác vụ nền (để chụp màn hình "Đang ... (Ns)")
        tok = tok.lstrip("~")
        if tok.startswith("BTN"):
            app.on_input("button", int(tok[3:]))
        elif tok.startswith("HAT"):
            app.on_input("hat", int(tok[3:]))
        elif tok != "WAIT":
            app.on_action(tok)
        if nowait:
            time.sleep(1.2)
        else:
            app.wait_task()   # headless: chờ tác vụ nền xong rồi mới sang bước kế


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = build_parser().parse_args(argv)
    if args.apps_dir:
        os.environ["LOONGMATE_APPS_DIR"] = args.apps_dir
    headless = bool(args.screenshot)
    if headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")

    probe = PlatformProbe(args.primary, args.secondary, args.userdata)
    vault = doctor = health = inv_engine = None
    engine_error = None
    if probe.primary_path is not None and probe.userdata_path is not None:
        try:
            inv_engine = InventoryEngine(probe.primary_path, probe.secondary_path)
            doctor = LibraryDoctor(probe.primary_path, probe.secondary_path, find_systems_catalog(), probe.userdata_path)
            vault = SaveVault(probe.primary_path, probe.secondary_path, probe.userdata_path)
            health = DeviceHealth(probe.primary_path, probe.secondary_path, probe.userdata_path)
        except Exception as e:
            engine_error = f"Khởi tạo engine thất bại: {e}"
    else:
        reasons = "; ".join(probe.probe_errors) if probe.probe_errors else "không xác định được thẻ Primary/userdata"
        engine_error = f"Không tìm thấy thẻ Primary hoặc userdata: {reasons}"

    try:
        sdl, ttf = load_sdl()
    except OSError as e:
        print(f"[LoongMate] Không nạp được SDL2/SDL2_ttf: {e}", file=sys.stderr)
        return 2

    def err() -> str:
        return sdl.SDL_GetError().decode("utf-8", "replace")

    if sdl.SDL_Init(SDL_INIT_VIDEO | (0 if headless else (SDL_INIT_JOYSTICK | SDL_INIT_GAMECONTROLLER))) != 0:
        print(f"[LoongMate] SDL_Init lỗi: {err()}", file=sys.stderr)
        return 2
    font_path = find_font()
    if font_path is None:
        print("[LoongMate] Không tìm thấy font nào (đã thử Nunito/Inter/IBMPlexSans dưới $UMRK_LAUNCHER_PATH và "
              "$SDCARD_PATH/.system/leaf/platforms/mlp1/launcher/res/fonts). Hành động: kiểm tra 2 biến môi trường này.",
              file=sys.stderr)
        sdl.SDL_Quit()
        return 2
    if ttf.TTF_Init() != 0:
        print(f"[LoongMate] TTF_Init lỗi: {err()}", file=sys.stderr)
        sdl.SDL_Quit()
        return 2
    fonts: Dict[str, Any] = {}
    for key, size in (("title", 28), ("body", 20), ("small", 17)):
        f = ttf.TTF_OpenFont(font_path.encode("utf-8"), size)
        if not f:
            print(f"[LoongMate] TTF_OpenFont lỗi ({font_path}, {size}): {err()}", file=sys.stderr)
            ttf.TTF_Quit()
            sdl.SDL_Quit()
            return 2
        fonts[key] = f

    window = sdl.SDL_CreateWindow(b"LoongMate", 0, 0, WIN_W, WIN_H,
                                  SDL_WINDOW_SHOWN if headless else SDL_WINDOW_FULLSCREEN_DESKTOP)
    if not window:
        print(f"[LoongMate] SDL_CreateWindow lỗi: {err()}", file=sys.stderr)
        return 2
    renderer = (sdl.SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED)
                or sdl.SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE))
    if not renderer:
        print(f"[LoongMate] SDL_CreateRenderer lỗi: {err()}", file=sys.stderr)
        sdl.SDL_DestroyWindow(window)
        return 2

    g = Gfx(sdl, ttf, renderer, fonts)
    app = App(probe, vault, doctor, inv_engine, health, probe.userdata_path, engine_error)
    opener = {"device": "open_device", "cards": "open_cards",
              "savevault": "open_savevault", "library": "open_library",
              "battery": "open_battery_health", "update": "open_update", "hottabs": "open_hottabs"}.get(args.screen)
    if opener:
        app.refresh_home()
        getattr(app, opener)()
        if headless:
            app.wait_task()   # open_battery_health tính trong thread nền: chờ xong mới chụp
    joystick = controller = None
    try:
        if headless:
            app.refresh_home()
            run_script(app, args.script)
            app.render(g)
            sdl.SDL_RenderPresent(renderer)
            write_ppm(sdl, renderer, args.screenshot, WIN_W, WIN_H)
            print(f"[LoongMate] Đã ghi screenshot: {args.screenshot}")
        else:
            if sdl.SDL_NumJoysticks() > 0:
                # CHỈ mở joystick, KHÔNG mở GameController. Mở GameController thì SDL gửi
                # MỖI lần bấm thành hai sự kiện (joystick + controller) với hai bảng số
                # khác nhau — D-pad thành nút 11–14 "lạ", bấm lần hai là thoát (lỗi thật
                # 2026-10-02). Leaf/Catastrophe trên mlp1 cũng dùng joystick API.
                joystick = sdl.SDL_JoystickOpen(0)
            app.refresh_home()
            rec = app.home_data.get("recovery")
            if rec:
                app.info("CẢNH BÁO: phục hồi dở dang", [
                    str(rec.get("message", "")),
                    "Hành động: mở SaveVault (chọn thẻ 3, bấm A), mục Rollback." if rec.get("can_rollback") else
                    "KHÔNG tìm thấy bản pre-restore để rollback tự động — kiểm tra dữ liệu thủ công qua SSH."])
            sdl.SDL_GetCurrentVideoDriver.restype = ctypes.c_char_p
            drv = (sdl.SDL_GetCurrentVideoDriver() or b"?").decode(errors="replace")
            print(f"[LoongMate] SDL sẵn sàng: driver={drv}, joystick={sdl.SDL_NumJoysticks()}", file=sys.stderr, flush=True)
            t0 = time.monotonic()
            trace_left = 80
            ev = SDL_Event()
            while app.running:
                while sdl.SDL_PollEvent(ctypes.byref(ev)):
                    t = ev.type
                    if trace_left > 0 and t in (SDL_JOYBUTTONDOWN, SDL_JOYHATMOTION, SDL_KEYDOWN):
                        trace_left -= 1
                        what = (f"nút joystick {ev.jbutton.button}" if t == SDL_JOYBUTTONDOWN else
                                f"hat value={ev.jhat.value}" if t == SDL_JOYHATMOTION else
                                f"phím sym={ev.key.keysym.sym}")
                        print(f"[LoongMate][sự kiện +{time.monotonic() - t0:.2f}s] {what}", file=sys.stderr, flush=True)
                    if t == SDL_QUIT:
                        app.exit_reason, app.running = "nhận SDL_QUIT", False
                    elif t == SDL_KEYDOWN:
                        app.on_input("key", ev.key.keysym.sym)
                    elif t == SDL_JOYBUTTONDOWN:
                        app.on_input("button", ev.jbutton.button)
                    elif t == SDL_JOYHATMOTION:
                        app.on_input("hat", ev.jhat.value)
                    elif t == SDL_JOYAXISMOTION:
                        v = ev.jaxis.value
                        app.on_input("axis", (ev.jaxis.axis, 1 if v > AXIS_THRESHOLD else (-1 if v < -AXIS_THRESHOLD else 0)))
                app.tick()
                app.render(g)
                sdl.SDL_RenderPresent(renderer)
                sdl.SDL_Delay(16)
            print(f"[LoongMate] Giao diện SDL thoát sau {time.monotonic() - t0:.2f}s, lý do: {app.exit_reason}",
                  file=sys.stderr, flush=True)
    finally:
        g.tc.clear()
        for f in fonts.values():
            ttf.TTF_CloseFont(f)
        ttf.TTF_Quit()
        if joystick:
            sdl.SDL_JoystickClose(joystick)
        if controller:
            sdl.SDL_GameControllerClose(controller)
        sdl.SDL_DestroyRenderer(renderer)
        sdl.SDL_DestroyWindow(window)
        sdl.SDL_Quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
