import sys, asyncio, threading, queue, json, ctypes, ctypes.wintypes
import tkinter as tk
from tkinter import messagebox
import websockets
import win32clipboard, win32con
import pystray
from PIL import Image, ImageDraw

RELAY_URL   = "wss://clipboard.35.209.129.27.sslip.io/ws"
TOKEN       = "clipsync-secret-2026"
DEVICE_NAME = "windows"
MAX_HISTORY = 50

history: list[str] = []
history_lock = threading.Lock()
last_text = ""
send_queue: asyncio.Queue = None
ws_loop: asyncio.AbstractEventLoop = None
ui_queue: queue.Queue = queue.Queue()
status_msg = "연결 중..."


# ── 클립보드 ──────────────────────────────────────────────
def get_clipboard() -> str:
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return ""


def set_clipboard(text: str):
    global last_text
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        last_text = text
    except Exception as e:
        print(f"[set_clipboard] {e}")


def add_history(text: str):
    with history_lock:
        if text in history:
            history.remove(text)
        history.insert(0, text)
        if len(history) > MAX_HISTORY:
            history.pop()
    ui_queue.put(("refresh",))


# ── 클립보드 모니터 (Win32) ───────────────────────────────
WM_CLIPBOARDUPDATE = 0x031D
WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.wintypes.HWND, ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint), ("lpfnWndProc", WNDPROCTYPE),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HANDLE), ("hIcon", ctypes.wintypes.HANDLE),
        ("hCursor", ctypes.wintypes.HANDLE), ("hbrBackground", ctypes.wintypes.HANDLE),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR), ("lpszClassName", ctypes.wintypes.LPCWSTR),
    ]

_u32 = ctypes.windll.user32
_u32.DefWindowProcW.restype  = ctypes.c_ssize_t
_u32.DefWindowProcW.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]


def clipboard_monitor():
    global last_text

    def wnd_proc(hwnd, msg, wparam, lparam):
        global last_text
        if msg == WM_CLIPBOARDUPDATE:
            text = get_clipboard()
            if text and text != last_text:
                last_text = text
                add_history(text)
                if send_queue and ws_loop:
                    asyncio.run_coroutine_threadsafe(send_queue.put(text), ws_loop)
        return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc  = WNDPROCTYPE(wnd_proc)
    hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
    cls   = "ClipSyncWatcher2"
    wc    = WNDCLASSW()
    wc.lpfnWndProc   = proc
    wc.hInstance     = hinst
    wc.lpszClassName = cls
    _u32.RegisterClassW(ctypes.byref(wc))
    hwnd = _u32.CreateWindowExW(0, cls, "ClipSync2", 0, 0, 0, 0, 0, 0, 0, hinst, None)
    _u32.AddClipboardFormatListener(hwnd)

    msg = ctypes.wintypes.MSG()
    while _u32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
        _u32.TranslateMessage(ctypes.byref(msg))
        _u32.DispatchMessageW(ctypes.byref(msg))


# ── WebSocket ─────────────────────────────────────────────
async def ws_run():
    global send_queue, last_text, status_msg
    send_queue = asyncio.Queue()

    while True:
        try:
            status_msg = "연결 중..."
            ui_queue.put(("status",))
            async with websockets.connect(RELAY_URL, ping_interval=30) as ws:
                await ws.send(json.dumps({"token": TOKEN, "name": DEVICE_NAME}))
                await ws.recv()
                status_msg = "연결됨"
                ui_queue.put(("status",))

                async def sender():
                    while True:
                        text = await send_queue.get()
                        await ws.send(json.dumps({"type": "clip", "text": text}))

                async def receiver():
                    global last_text
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "clip":
                            text = msg.get("text", "")
                            if text and text != last_text:
                                last_text = text
                                set_clipboard(text)
                                add_history(text)

                await asyncio.gather(sender(), receiver())

        except Exception as e:
            status_msg = f"연결 끊김 — 재연결 중..."
            ui_queue.put(("status",))
            await asyncio.sleep(5)


# ── 스크롤 가능한 히스토리 패널 ──────────────────────────
BG_NORMAL   = "#ffffff"
BG_SELECTED = "#bbdefb"
BG_HOVER    = "#e3f2fd"
BG_SEP      = "#e0e0e0"

class HistoryPanel(tk.Frame):
    def __init__(self, parent, on_copy, on_edit, on_delete):
        super().__init__(parent, bg=BG_NORMAL)
        self._on_copy   = on_copy
        self._on_edit   = on_edit
        self._on_delete = on_delete
        self._selected  = -1
        self._rows: list[tuple[tk.Frame, tk.Label]] = []
        self._filtered: list[str] = []

        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, bg=BG_NORMAL)
        self._sb     = tk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._inner  = tk.Frame(self._canvas, bg=BG_NORMAL)
        self._win_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")

        self._inner.bind("<Configure>",  self._sync_scroll_region)
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self._canvas.bind("<MouseWheel>", self._scroll)
        self._inner.bind("<MouseWheel>",  self._scroll)

    def _sync_scroll_region(self, _=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_resize(self, e):
        self._canvas.itemconfig(self._win_id, width=e.width)
        wrap = max(100, e.width - 24)
        for _, lbl in self._rows:
            lbl.configure(wraplength=wrap)

    def _scroll(self, e):
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def refresh(self, items: list[str]):
        for frame, _ in self._rows:
            frame.destroy()
        self._rows.clear()
        self._filtered = list(items)
        self._selected = -1

        wrap = max(100, self._canvas.winfo_width() - 24)

        for idx, text in enumerate(items):
            frame = tk.Frame(self._inner, bg=BG_NORMAL, cursor="hand2")
            frame.pack(fill="x")

            lbl = tk.Label(
                frame, text=text, font=("Segoe UI", 10),
                bg=BG_NORMAL, fg="#212121", anchor="w", justify="left",
                wraplength=wrap, padx=8, pady=6,
            )
            lbl.pack(fill="x", side="left", expand=True)

            sep = tk.Frame(self._inner, bg=BG_SEP, height=1)
            sep.pack(fill="x")

            i = idx
            for w in (frame, lbl):
                w.bind("<Button-1>",        lambda e, n=i: self._select(n))
                w.bind("<Double-Button-1>", lambda e, n=i: self._on_copy(n))
                w.bind("<Enter>",           lambda e, f=frame, n=i: self._hover(f, n, True))
                w.bind("<Leave>",           lambda e, f=frame, n=i: self._hover(f, n, False))
                w.bind("<MouseWheel>",      self._scroll)

            self._rows.append((frame, lbl))

    def _select(self, idx):
        if 0 <= self._selected < len(self._rows):
            f, l = self._rows[self._selected]
            f.configure(bg=BG_NORMAL); l.configure(bg=BG_NORMAL)
        self._selected = idx
        if 0 <= idx < len(self._rows):
            f, l = self._rows[idx]
            f.configure(bg=BG_SELECTED); l.configure(bg=BG_SELECTED)

    def _hover(self, frame, idx, entering):
        if idx == self._selected:
            return
        bg = BG_HOVER if entering else BG_NORMAL
        frame.configure(bg=bg)
        for c in frame.winfo_children():
            c.configure(bg=bg)

    def get_selected_idx(self) -> int:
        return self._selected

    def get_selected_text(self) -> str | None:
        i = self._selected
        if 0 <= i < len(self._filtered):
            return self._filtered[i]
        return None


# ── 수정 다이얼로그 ───────────────────────────────────────
def open_edit_dialog(parent, original: str, on_save):
    dlg = tk.Toplevel(parent)
    dlg.title("내용 수정")
    dlg.geometry("480x300")
    dlg.grab_set()

    tk.Label(dlg, text="내용을 수정하세요:", font=("Segoe UI", 10), anchor="w").pack(fill="x", padx=12, pady=(12, 4))

    txt = tk.Text(dlg, font=("Segoe UI", 10), wrap="word", relief="solid", borderwidth=1)
    txt.pack(fill="both", expand=True, padx=12, pady=4)
    txt.insert("1.0", original)
    txt.focus_set()

    def save():
        new_text = txt.get("1.0", "end-1c").strip()
        if new_text:
            on_save(new_text)
        dlg.destroy()

    btn_frame = tk.Frame(dlg)
    btn_frame.pack(fill="x", padx=12, pady=(4, 12))
    tk.Button(btn_frame, text="저장", command=save, font=("Segoe UI", 10), width=10,
              bg="#1976D2", fg="white", relief="flat", padx=4, pady=4).pack(side="left", padx=4)
    tk.Button(btn_frame, text="취소", command=dlg.destroy, font=("Segoe UI", 10), width=10,
              relief="flat", padx=4, pady=4).pack(side="left")

    dlg.bind("<Escape>", lambda e: dlg.destroy())


# ── 히스토리 창 ───────────────────────────────────────────
class HistoryWindow:
    def __init__(self, root: tk.Tk):
        self.root   = root
        self.window = None
        self.panel  = None

    def show(self):
        if self.window and self.window.winfo_exists():
            self.window.lift(); self.window.focus_force(); return
        self._build()

    def _build(self):
        win = tk.Toplevel(self.root)
        win.title("ClipSync 히스토리")
        win.geometry("500x560")
        self.window = win

        # ── 메모 입력창 ──
        input_frame = tk.Frame(win, bg="#f5f5f5", pady=6)
        input_frame.pack(fill="x", padx=8, pady=(8, 0))
        tk.Label(input_frame, text="메모 입력 후 Enter:", font=("Segoe UI", 9),
                 bg="#f5f5f5", fg="#555").pack(anchor="w", padx=4)
        self.input_var = tk.StringVar()
        entry = tk.Entry(input_frame, textvariable=self.input_var,
                         font=("Segoe UI", 11), relief="solid")
        entry.pack(fill="x", padx=4, pady=(2, 0))
        entry.bind("<Return>", self._on_input_enter)

        # ── 히스토리 패널 ──
        self.panel = HistoryPanel(
            win,
            on_copy=self._copy_item,
            on_edit=self._edit_item,
            on_delete=self._delete_item,
        )
        self.panel.pack(fill="both", expand=True, padx=8, pady=6)

        # ── 버튼 ──
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", padx=8, pady=(0, 6))
        for text, cmd in [
            ("복사",     self._copy_selected),
            ("수정",     self._edit_selected),
            ("삭제",     self._delete_selected),
            ("전체 삭제", self._clear_all),
        ]:
            tk.Button(btn_frame, text=text, command=cmd,
                      font=("Segoe UI", 9), relief="flat",
                      bg="#e0e0e0", padx=8, pady=4).pack(side="left", padx=2)

        # ── 상태 바 ──
        self.status_var = tk.StringVar(value=status_msg)
        tk.Label(win, textvariable=self.status_var, anchor="w",
                 fg="#777", font=("Segoe UI", 8)).pack(fill="x", padx=10, pady=(0, 4))

        self.refresh()

    def refresh(self):
        if not (self.window and self.window.winfo_exists()): return
        with history_lock:
            items = list(history)
        if self.panel:
            self.panel.refresh(items)

    def update_status(self):
        if self.window and self.window.winfo_exists():
            self.status_var.set(status_msg)

    def _on_input_enter(self, _=None):
        text = self.input_var.get().strip()
        if not text: return
        self.input_var.set("")
        add_history(text)
        set_clipboard(text)
        if send_queue and ws_loop:
            asyncio.run_coroutine_threadsafe(send_queue.put(text), ws_loop)

    def _copy_item(self, idx):
        if not self.panel: return
        with history_lock:
            items = list(history)
        if 0 <= idx < len(items):
            text = items[idx]
            set_clipboard(text)
            if send_queue and ws_loop:
                asyncio.run_coroutine_threadsafe(send_queue.put(text), ws_loop)

    def _copy_selected(self):
        if not self.panel: return
        text = self.panel.get_selected_text()
        if text:
            set_clipboard(text)
            if send_queue and ws_loop:
                asyncio.run_coroutine_threadsafe(send_queue.put(text), ws_loop)

    def _edit_item(self, idx):
        with history_lock:
            items = list(history)
        if 0 <= idx < len(items):
            original = items[idx]
            def save(new_text):
                with history_lock:
                    try:
                        pos = history.index(original)
                        history[pos] = new_text
                    except ValueError:
                        pass
                set_clipboard(new_text)
                if send_queue and ws_loop:
                    asyncio.run_coroutine_threadsafe(send_queue.put(new_text), ws_loop)
                self.refresh()
            open_edit_dialog(self.window, original, save)

    def _edit_selected(self):
        if not self.panel: return
        idx = self.panel.get_selected_idx()
        if idx >= 0:
            self._edit_item(idx)

    def _delete_item(self, idx):
        with history_lock:
            items = list(history)
        if 0 <= idx < len(items):
            with history_lock:
                try: history.remove(items[idx])
                except ValueError: pass
            self.refresh()

    def _delete_selected(self):
        if not self.panel: return
        idx = self.panel.get_selected_idx()
        if idx >= 0:
            self._delete_item(idx)

    def _clear_all(self):
        if messagebox.askyesno("확인", "히스토리를 모두 삭제할까요?"):
            with history_lock:
                history.clear()
            self.refresh()


# ── 트레이 아이콘 ─────────────────────────────────────────
def make_icon_image():
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    d.ellipse([4, 4, size-4, size-4], fill="#1976D2")
    d.rectangle([18, 22, 46, 42], fill="white")
    d.line([22, 34, 42, 34], fill="#1976D2", width=3)
    d.line([22, 28, 36, 28], fill="#1976D2", width=3)
    return img


# ── 메인 ──────────────────────────────────────────────────
def main():
    ws_loop_ref = asyncio.new_event_loop()
    global ws_loop
    ws_loop = ws_loop_ref

    root = tk.Tk()
    root.withdraw()

    hw = HistoryWindow(root)

    threading.Thread(target=lambda: ws_loop_ref.run_until_complete(ws_run()), daemon=True).start()
    threading.Thread(target=clipboard_monitor, daemon=True).start()

    def on_show(_icon, _item):
        root.after(0, hw.show)

    def on_quit(_icon, _item):
        _icon.stop()
        root.quit()

    icon = pystray.Icon(
        "ClipSync", make_icon_image(), "ClipSync",
        pystray.Menu(
            pystray.MenuItem("히스토리 보기", on_show, default=True),
            pystray.MenuItem("종료", on_quit),
        ),
    )
    icon.run_detached()

    def poll():
        try:
            while True:
                ev = ui_queue.get_nowait()
                if ev[0] == "refresh": hw.refresh()
                elif ev[0] == "status": hw.update_status()
        except queue.Empty:
            pass
        root.after(300, poll)

    root.after(300, poll)
    root.mainloop()
    icon.stop()


if __name__ == "__main__":
    main()
