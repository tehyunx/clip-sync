import sys, asyncio, threading, queue, json, ctypes, ctypes.wintypes, time
import tkinter as tk
from tkinter import ttk, messagebox
import websockets
import win32clipboard, win32con
import pystray
from PIL import Image, ImageDraw, ImageFont

RELAY_URL   = "wss://clipboard.35.209.129.27.sslip.io/ws"
TOKEN       = "clipsync-secret-2026"
DEVICE_NAME = "windows"
MAX_HISTORY = 50

# ── 공유 상태 ──────────────────────────────────────────────
history: list[str] = []
history_lock = threading.Lock()
last_text = ""
send_queue: asyncio.Queue = None
ws_loop: asyncio.AbstractEventLoop = None
ui_queue: queue.Queue = queue.Queue()   # 메인 스레드로 이벤트 전달
status_msg = "연결 중..."


# ── 클립보드 유틸 ──────────────────────────────────────────
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


# ── 클립보드 모니터 (Win32 메시지 루프) ───────────────────
WM_CLIPBOARDUPDATE = 0x031D

WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.wintypes.HWND, ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style",         ctypes.c_uint),
        ("lpfnWndProc",   WNDPROCTYPE),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.wintypes.HANDLE),
        ("hIcon",         ctypes.wintypes.HANDLE),
        ("hCursor",       ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HANDLE),
        ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
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


# ── WebSocket (asyncio 스레드) ─────────────────────────────
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
            status_msg = f"연결 끊김: {e}"
            ui_queue.put(("status",))
            await asyncio.sleep(5)


# ── 트레이 아이콘 이미지 생성 ─────────────────────────────
def make_icon_image():
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    d.ellipse([4, 4, size-4, size-4], fill="#1976D2")
    d.rectangle([18, 22, 46, 42], fill="white", outline="white")
    d.line([22, 34, 42, 34], fill="#1976D2", width=3)
    d.line([22, 28, 36, 28], fill="#1976D2", width=3)
    return img


# ── 히스토리 창 ───────────────────────────────────────────
class HistoryWindow:
    def __init__(self, root: tk.Tk):
        self.root   = root
        self.window = None

    def show(self):
        if self.window and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
            return
        self._build()

    def _build(self):
        win = tk.Toplevel(self.root)
        win.title("ClipSync 히스토리")
        win.geometry("480x520")
        win.resizable(True, True)
        self.window = win

        # 상태 표시줄
        self.status_var = tk.StringVar(value=status_msg)
        status_bar = tk.Label(win, textvariable=self.status_var,
                              anchor="w", fg="#555", font=("Segoe UI", 9))
        status_bar.pack(fill="x", padx=8, pady=(4, 0))

        # 검색창
        search_frame = tk.Frame(win)
        search_frame.pack(fill="x", padx=8, pady=4)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        tk.Entry(search_frame, textvariable=self.search_var,
                 font=("Segoe UI", 10), relief="solid").pack(fill="x")

        # 리스트
        list_frame = tk.Frame(win)
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        sb = tk.Scrollbar(list_frame)
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(list_frame, font=("Segoe UI", 10),
                                  yscrollcommand=sb.set, selectmode="single",
                                  activestyle="dotbox", relief="solid")
        self.listbox.pack(fill="both", expand=True)
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self._on_select)
        self.listbox.bind("<Return>", self._on_select)

        # 버튼
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(btn_frame, text="복사", command=self._on_select,
                  font=("Segoe UI", 9), width=8).pack(side="left", padx=2)
        tk.Button(btn_frame, text="삭제", command=self._delete_selected,
                  font=("Segoe UI", 9), width=8).pack(side="left", padx=2)
        tk.Button(btn_frame, text="전체 삭제", command=self._clear_all,
                  font=("Segoe UI", 9), width=8).pack(side="left", padx=2)

        self.status_var_ref = status_bar
        self.refresh()

    def refresh(self):
        if not (self.window and self.window.winfo_exists()):
            return
        query = self.search_var.get().lower() if hasattr(self, "search_var") else ""
        self.listbox.delete(0, "end")
        with history_lock:
            items = list(history)
        for item in items:
            if query in item.lower():
                preview = item.replace("\n", " ").replace("\r", "")[:80]
                self.listbox.insert("end", preview)

    def update_status(self):
        if self.window and self.window.winfo_exists() and hasattr(self, "status_var"):
            self.status_var.set(status_msg)

    def _on_select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        query = self.search_var.get().lower()
        with history_lock:
            items = [h for h in history if query in h.lower()]
        if idx < len(items):
            text = items[idx]
            set_clipboard(text)
            if send_queue and ws_loop:
                asyncio.run_coroutine_threadsafe(send_queue.put(text), ws_loop)

    def _delete_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        query = self.search_var.get().lower()
        with history_lock:
            items = [h for h in history if query in h.lower()]
        if idx < len(items):
            with history_lock:
                try:
                    history.remove(items[idx])
                except ValueError:
                    pass
        self.refresh()

    def _clear_all(self):
        if messagebox.askyesno("확인", "히스토리를 모두 삭제할까요?"):
            with history_lock:
                history.clear()
            self.refresh()


# ── 메인 ──────────────────────────────────────────────────
def main():
    global ws_loop

    # 숨겨진 루트 창 (tkinter 이벤트 루프용)
    root = tk.Tk()
    root.withdraw()

    hw = HistoryWindow(root)

    # asyncio 스레드
    ws_loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: ws_loop.run_until_complete(ws_run()), daemon=True).start()

    # 클립보드 모니터 스레드
    threading.Thread(target=clipboard_monitor, daemon=True).start()

    # 트레이 메뉴
    def on_show(_icon, _item):
        root.after(0, hw.show)

    def on_quit(_icon, _item):
        _icon.stop()
        root.quit()

    tray_menu = pystray.Menu(
        pystray.MenuItem("히스토리 보기", on_show, default=True),
        pystray.MenuItem("종료", on_quit),
    )
    icon = pystray.Icon("ClipSync", make_icon_image(), "ClipSync", tray_menu)
    icon.run_detached()

    # ui_queue 폴링 (tkinter after 루프)
    def poll_ui():
        try:
            while True:
                event = ui_queue.get_nowait()
                if event[0] == "refresh":
                    hw.refresh()
                elif event[0] == "status":
                    hw.update_status()
        except queue.Empty:
            pass
        root.after(300, poll_ui)

    root.after(300, poll_ui)
    root.mainloop()
    icon.stop()


if __name__ == "__main__":
    main()
