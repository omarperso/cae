#!/usr/bin/env python3
"""
CAE Node — Claude Agent Everywhere.

Runs on a computer you want to control from your main Claude. It:
  - advertises itself on the local network (mDNS / Zeroconf),
  - requires a one-time pairing code before accepting any command,
  - exposes a small, authenticated HTTP API for screenshots, a live screen
    stream, mouse/keyboard, and shell commands,
  - shows a visible banner on screen while a controller is connected.

Everything stays on the local network. Nothing is sent to an external server.

Run:
    python cae_node.py --name "Omar Thinkpad"
"""

import argparse
import base64
import hmac
import io
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import threading
import time
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- Optional deps (installed by the one-line installer) --------------------
try:
    from zeroconf import ServiceInfo, Zeroconf
except ImportError:
    Zeroconf = None
try:
    import mss  # fast screen capture
    import mss.tools
except ImportError:
    mss = None
try:
    import pyautogui  # mouse / keyboard
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".cae")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token")
PORT = 8765
SERVICE_TYPE = "_cae._tcp.local."


# ---- Pairing / auth ---------------------------------------------------------
def load_or_create_token():
    """A long-lived token proving this controller was paired on this machine."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    token = secrets.token_urlsafe(32)
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


class State:
    """Shared runtime state for the node."""

    def __init__(self, name, token):
        self.name = name
        self.token = token
        self.pairing_code = None          # set while a pairing window is open
        self.pairing_expires = 0
        self.controller_active = False    # drives the on-screen banner
        self.last_seen = 0


STATE = None  # populated in main()


def open_pairing_window(seconds=120):
    """Show a 6-digit code the user reads out to pair a new controller."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    STATE.pairing_code = code
    STATE.pairing_expires = time.time() + seconds
    print("\n" + "=" * 44)
    print(f"  PAIRING CODE for this computer: {code}")
    print(f"  Type it on your main Claude within {seconds}s.")
    print("=" * 44 + "\n")
    return code


# ---- HTTP handler -----------------------------------------------------------
def require_token(fn):
    @wraps(fn)
    def wrapper(self, *a, **k):
        sent = self.headers.get("X-CAE-Token", "")
        if not hmac.compare_digest(sent, STATE.token):
            self._json(401, {"error": "not paired or bad token"})
            return
        STATE.last_seen = time.time()
        return fn(self, *a, **k)
    return wrapper


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the console clean
        pass

    # --- helpers ---
    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _grab_png(self):
        if mss is None:
            return None
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])
            return mss.tools.to_png(shot.rgb, shot.size)

    def _html(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _status_page(self):
        """A simple local interface the installer opens in the browser: shows
        the device name, pairing code, and live state."""
        code = STATE.pairing_code if (STATE.pairing_code and
                                      time.time() < STATE.pairing_expires) else None
        connected = STATE.controller_active
        code_block = (
            f'<div class="code">{code}</div><p class="muted">Type this code on '
            f'your main Claude to pair. Expires in ~2&nbsp;min.</p>'
            if code else
            '<p class="muted">No pairing window open. This computer is paired and '
            'ready, or press Enter in the terminal to open a new code.</p>')
        dot = "#2ecc71" if connected else "#7f8c8d"
        state = "A controller is connected right now" if connected else "Idle — waiting"
        return f"""<!doctype html><meta charset=utf-8>
<title>CAE — {STATE.name}</title>
<meta http-equiv=refresh content=3>
<style>
 body{{font-family:system-ui,Segoe UI,Arial;background:#0f1115;color:#e8e8e8;
      display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
 .card{{background:#181b22;border:1px solid #262a33;border-radius:16px;padding:32px 40px;
       max-width:460px;text-align:center}}
 h1{{font-size:20px;margin:0 0 4px}} .muted{{color:#9aa4b2;font-size:14px}}
 .code{{font-size:48px;letter-spacing:10px;font-weight:700;margin:18px 0;color:#4da3ff}}
 .dot{{height:10px;width:10px;border-radius:50%;background:{dot};display:inline-block;
       margin-right:6px}} .os{{margin-top:18px;font-size:13px;color:#6b7280}}
</style>
<div class=card>
 <h1>🌐 Claude Agent Everywhere</h1>
 <div class=muted>{STATE.name} · {platform.system()}</div>
 {code_block}
 <div class=os><span class=dot></span>{state}</div>
</div>"""

    # --- routes ---
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._html(200, self._status_page())
        elif self.path == "/ping":
            self._json(200, {
                "name": STATE.name,
                "os": platform.system(),
                "paired": True,
                "controller_active": STATE.controller_active,
            })
        elif self.path == "/screenshot":
            self._screenshot()
        elif self.path == "/stream":
            self._stream()
        else:
            self._json(404, {"error": "unknown route"})

    def do_POST(self):
        if self.path == "/pair":
            self._pair()
        elif self.path == "/connect":
            self._connect()
        elif self.path == "/disconnect":
            self._disconnect()
        elif self.path == "/click":
            self._click()
        elif self.path == "/type":
            self._type()
        elif self.path == "/key":
            self._key()
        elif self.path == "/shell":
            self._shell()
        else:
            self._json(404, {"error": "unknown route"})

    # --- pairing (no token yet) ---
    def _pair(self):
        body = self._read_body()
        code = str(body.get("code", ""))
        if (STATE.pairing_code and time.time() < STATE.pairing_expires
                and hmac.compare_digest(code, STATE.pairing_code)):
            STATE.pairing_code = None  # single use
            self._json(200, {"token": STATE.token, "name": STATE.name})
        else:
            self._json(403, {"error": "no pairing window open, or wrong code"})

    # --- authenticated routes ---
    @require_token
    def _connect(self):
        STATE.controller_active = True
        self._json(200, {"ok": True, "name": STATE.name})

    @require_token
    def _disconnect(self):
        STATE.controller_active = False
        self._json(200, {"ok": True})

    @require_token
    def _screenshot(self):
        png = self._grab_png()
        if png is None:
            self._json(503, {"error": "screen capture unavailable (install mss)"})
            return
        self._json(200, {"png_base64": base64.b64encode(png).decode()})

    @require_token
    def _stream(self):
        """MJPEG stream the user can open in a browser to watch live."""
        if mss is None:
            self._json(503, {"error": "streaming unavailable (install mss)"})
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            while STATE.controller_active:
                png = self._grab_png()
                if png is None:
                    break
                self.wfile.write(b"--frame\r\nContent-Type: image/png\r\n")
                self.wfile.write(f"Content-Length: {len(png)}\r\n\r\n".encode())
                self.wfile.write(png + b"\r\n")
                time.sleep(0.4)  # ~2.5 fps, light on the network
        except (BrokenPipeError, ConnectionResetError):
            pass

    @require_token
    def _click(self):
        if pyautogui is None:
            self._json(503, {"error": "input unavailable (install pyautogui)"})
            return
        b = self._read_body()
        x, y = int(b.get("x", 0)), int(b.get("y", 0))
        pyautogui.click(x, y, button=b.get("button", "left"),
                        clicks=int(b.get("clicks", 1)))
        self._json(200, {"ok": True})

    @require_token
    def _type(self):
        if pyautogui is None:
            self._json(503, {"error": "input unavailable"})
            return
        pyautogui.typewrite(str(self._read_body().get("text", "")), interval=0.01)
        self._json(200, {"ok": True})

    @require_token
    def _key(self):
        if pyautogui is None:
            self._json(503, {"error": "input unavailable"})
            return
        keys = self._read_body().get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        pyautogui.hotkey(*keys)
        self._json(200, {"ok": True})

    @require_token
    def _shell(self):
        b = self._read_body()
        cmd = b.get("cmd", "")
        if not cmd:
            self._json(400, {"error": "no command"})
            return
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True,
                                 text=True, timeout=int(b.get("timeout", 120)))
            self._json(200, {
                "returncode": out.returncode,
                "stdout": out.stdout[-20000:],
                "stderr": out.stderr[-20000:],
            })
        except subprocess.TimeoutExpired:
            self._json(408, {"error": "command timed out"})


# ---- Banner ("Claude is controlling this PC") -------------------------------
def banner_loop():
    """Best-effort visible indicator. Falls back to console if no GUI toolkit."""
    try:
        import tkinter as tk
    except ImportError:
        last = None
        while True:
            if STATE.controller_active != last:
                print("🔴 CAE: a controller is connected"
                      if STATE.controller_active
                      else "⚪ CAE: idle")
                last = STATE.controller_active
            time.sleep(1)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.9)
    except tk.TclError:
        pass
    root.configure(bg="#c0392b")
    w = root.winfo_screenwidth()
    root.geometry(f"{w}x28+0+0")
    label = tk.Label(root, text="🔴  Claude is controlling this computer (CAE)  —  move mouse to bottom-left to stop",
                     bg="#c0392b", fg="white", font=("Segoe UI", 11, "bold"))
    label.pack(fill="both", expand=True)

    def tick():
        if STATE.controller_active:
            root.deiconify()
        else:
            root.withdraw()
        # emergency stop: cursor slammed into bottom-left corner
        if pyautogui is not None:
            try:
                x, y = pyautogui.position()
                if x < 3 and y > root.winfo_screenheight() - 3:
                    STATE.controller_active = False
            except Exception:
                pass
        root.after(400, tick)

    root.withdraw()
    tick()
    root.mainloop()


# ---- mDNS advertisement -----------------------------------------------------
def advertise(name, port):
    if Zeroconf is None:
        print("(zeroconf not installed — discovery disabled, connect by IP)")
        return None
    ip = socket.gethostbyname(socket.gethostname())
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"name": name, "os": platform.system()},
    )
    zc = Zeroconf()
    zc.register_service(info)
    print(f"Advertising '{name}' at {ip}:{port} on the local network.")
    return zc


def main():
    global STATE
    ap = argparse.ArgumentParser(description="CAE Node")
    ap.add_argument("--name", default=socket.gethostname(),
                    help="friendly name shown to the controller")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--pair", action="store_true",
                    help="open a pairing window immediately on start")
    args = ap.parse_args()

    STATE = State(args.name, load_or_create_token())

    threading.Thread(target=banner_loop, daemon=True).start()
    advertise(args.name, args.port)
    open_pairing_window()  # first run always needs a pairing

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"CAE Node '{args.name}' listening on :{args.port}. Ctrl+C to stop.")
    print("Press Enter to open a new pairing window at any time.")

    def console():
        for _ in sys.stdin:
            open_pairing_window()
    threading.Thread(target=console, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCAE Node stopped.")


if __name__ == "__main__":
    main()
