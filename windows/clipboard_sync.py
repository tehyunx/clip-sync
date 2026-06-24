import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import asyncio, ctypes, ctypes.wintypes, json, threading
import websockets
import win32clipboard, win32con

RELAY_URL   = "wss://clipboard.35.209.129.27.sslip.io/ws"
TOKEN       = "clipsync-secret-2026"
DEVICE_NAME = "windows"

last_text   = ""
send_queue: asyncio.Queue = None
ws_loop:    asyncio.AbstractEventLoop = None


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
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
    except Exception as e:
        print(f"[set_clipboard] {e}")


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
                if send_queue and ws_loop:
                    asyncio.run_coroutine_threadsafe(send_queue.put(text), ws_loop)
        return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc  = WNDPROCTYPE(wnd_proc)
    hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
    cls   = "ClipSyncWatcher"
    wc    = WNDCLASSW()
    wc.lpfnWndProc  = proc
    wc.hInstance    = hinst
    wc.lpszClassName = cls
    _u32.RegisterClassW(ctypes.byref(wc))
    hwnd = _u32.CreateWindowExW(0, cls, "ClipSync", 0, 0,0,0,0, 0,0, hinst, None)
    _u32.AddClipboardFormatListener(hwnd)
    print("[ClipSync] clipboard monitor started")

    msg = ctypes.wintypes.MSG()
    while _u32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
        _u32.TranslateMessage(ctypes.byref(msg))
        _u32.DispatchMessageW(ctypes.byref(msg))


async def ws_run():
    global send_queue, last_text
    send_queue = asyncio.Queue()

    while True:
        try:
            async with websockets.connect(RELAY_URL, ping_interval=30) as ws:
                await ws.send(json.dumps({"token": TOKEN, "name": DEVICE_NAME}))
                await ws.recv()
                print("[ClipSync] connected")

                async def sender():
                    while True:
                        text = await send_queue.get()
                        await ws.send(json.dumps({"type": "clip", "text": text}))
                        print(f"[->] {text[:50]!r}")

                async def receiver():
                    global last_text
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "clip":
                            text = msg.get("text", "")
                            if text and text != last_text:
                                last_text = text
                                set_clipboard(text)
                                print(f"[<-] {text[:50]!r}")

                await asyncio.gather(sender(), receiver())

        except Exception as e:
            print(f"[!] {e} — 5초 후 재연결")
            await asyncio.sleep(5)


if __name__ == "__main__":
    ws_loop = asyncio.new_event_loop()

    threading.Thread(target=clipboard_monitor, daemon=True).start()
    ws_loop.run_until_complete(ws_run())
