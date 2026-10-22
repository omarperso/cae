#!/usr/bin/env python3
"""
CAE Hub — Claude Agent Everywhere (controller side).

An MCP server that runs on your MAIN computer, next to Claude. It gives Claude
the tools to discover, pair with, watch, and drive the CAE Nodes running on
your other computers on the same local network.

Tools exposed to Claude:
    list_devices()                      -> devices seen on the network
    pair_device(name, code)             -> store the token after pairing
    paired_devices()                    -> devices you can already control
    connect(name) / disconnect(name)    -> toggle control + on-screen banner
    live_url(name)                      -> URL to watch the device live
    screenshot(name)                    -> one frame, returned to Claude
    click(name, x, y) / type_text(...)  -> input
    press_keys(name, keys)              -> hotkey, e.g. ["ctrl","s"]
    run_shell(name, cmd)                -> run a command, get stdout/stderr

Tokens are stored per device in ~/.cae/hub.json so you only pair once.
"""

import json
import os
import threading
import time

import requests
from mcp.server.fastmcp import FastMCP

try:
    from zeroconf import ServiceBrowser, Zeroconf
except ImportError:
    Zeroconf = None

HUB_STORE = os.path.join(os.path.expanduser("~"), ".cae", "hub.json")
SERVICE_TYPE = "_cae._tcp.local."
mcp = FastMCP("cae-hub")


# ---- token store ------------------------------------------------------------
def _load():
    if os.path.exists(HUB_STORE):
        with open(HUB_STORE) as f:
            return json.load(f)
    return {}


def _save(data):
    os.makedirs(os.path.dirname(HUB_STORE), exist_ok=True)
    with open(HUB_STORE, "w") as f:
        json.dump(data, f, indent=2)


def _device(name):
    d = _load().get(name)
    if not d:
        raise ValueError(f"'{name}' is not paired yet. Pair it first.")
    return d


def _headers(name):
    return {"X-CAE-Token": _device(name)["token"]}


def _base(name):
    d = _device(name)
    return f"http://{d['host']}:{d['port']}"


# ---- discovery --------------------------------------------------------------
def _discover(timeout=3.0):
    if Zeroconf is None:
        return []
    found = {}

    class L:
        def add_service(self, zc, type_, nm):
            info = zc.get_service_info(type_, nm)
            if not info or not info.addresses:
                return
            import socket
            ip = socket.inet_ntoa(info.addresses[0])
            label = info.properties.get(b"name", b"").decode() or nm.split(".")[0]
            found[label] = {"host": ip, "port": info.port,
                            "os": info.properties.get(b"os", b"?").decode()}

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    ServiceBrowser(zc, SERVICE_TYPE, L())
    time.sleep(timeout)
    zc.close()
    return [{"name": k, **v} for k, v in found.items()]


# ---- tools ------------------------------------------------------------------
@mcp.tool()
def list_devices() -> str:
    """Scan the local network for CAE Nodes. Returns name, IP, OS, and whether
    each one is already paired."""
    paired = set(_load().keys())
    devices = _discover()
    for d in devices:
        d["paired"] = d["name"] in paired
    if not devices:
        return ("No CAE devices found. Make sure the other computer is on the "
                "same Wi-Fi and the one-line installer has been run there.")
    return json.dumps(devices, indent=2)


@mcp.tool()
def pair_device(name: str, code: str, host: str = "", port: int = 8765) -> str:
    """Pair with a device using the 6-digit code it shows on its own screen.
    Needed once per device; the token is saved afterwards. If the device does
    not show up in list_devices (mDNS blocked by a firewall), pass its IP as
    host to pair directly."""
    if not host:
        disc = {d["name"]: d for d in _discover()}
        if name not in disc:
            return (f"'{name}' not visible on the network right now. If it is "
                    "running, pass its IP address as host to pair directly.")
        host, port = disc[name]["host"], disc[name]["port"]
    try:
        r = requests.post(f"http://{host}:{port}/pair", json={"code": code},
                          timeout=10)
    except requests.RequestException as e:
        return f"Could not reach {host}:{port}: {e}"
    if r.status_code != 200:
        return f"Pairing failed: {r.json().get('error', r.text)}"
    store = _load()
    store[name] = {"host": host, "port": port, "token": r.json()["token"]}
    _save(store)
    return f"Paired with '{name}'. You can now control it."


@mcp.tool()
def paired_devices() -> str:
    """List devices already paired (controllable without a code)."""
    store = _load()
    return json.dumps([{"name": k, "host": v["host"]} for k, v in store.items()],
                      indent=2) or "No devices paired yet."


@mcp.tool()
def connect(name: str) -> str:
    """Start controlling a device. Shows the red 'Claude is controlling this
    computer' banner on it. Always do this before input or streaming."""
    requests.post(f"{_base(name)}/connect", headers=_headers(name), timeout=10)
    return f"Connected to '{name}'. The banner is now visible on that computer."


@mcp.tool()
def disconnect(name: str) -> str:
    """Stop controlling a device and hide its banner."""
    requests.post(f"{_base(name)}/disconnect", headers=_headers(name), timeout=10)
    return f"Disconnected from '{name}'."


@mcp.tool()
def live_url(name: str) -> str:
    """Return a URL the user can open in a browser to watch the device live
    (MJPEG stream). The device must be connected first."""
    d = _device(name)
    return f"http://{d['host']}:{d['port']}/stream  (open this in a browser to watch '{name}' live)"


@mcp.tool()
def screenshot(name: str) -> str:
    """Capture one screenshot of the device. Returns a base64 PNG so Claude can
    see what is on screen."""
    r = requests.get(f"{_base(name)}/screenshot", headers=_headers(name), timeout=15)
    if r.status_code != 200:
        return f"Error: {r.json().get('error', r.text)}"
    return r.json()["png_base64"]


@mcp.tool()
def click(name: str, x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """Click at pixel (x, y) on the device's screen."""
    requests.post(f"{_base(name)}/click", headers=_headers(name),
                  json={"x": x, "y": y, "button": button, "clicks": clicks}, timeout=10)
    return f"Clicked ({x},{y}) on '{name}'."


@mcp.tool()
def type_text(name: str, text: str) -> str:
    """Type text on the device (into whatever field has focus)."""
    requests.post(f"{_base(name)}/type", headers=_headers(name),
                  json={"text": text}, timeout=30)
    return f"Typed {len(text)} chars on '{name}'."


@mcp.tool()
def press_keys(name: str, keys: list) -> str:
    """Press a key combo, e.g. ["ctrl","s"] or ["enter"]."""
    requests.post(f"{_base(name)}/key", headers=_headers(name),
                  json={"keys": keys}, timeout=10)
    return f"Pressed {'+'.join(keys)} on '{name}'."


@mcp.tool()
def run_shell(name: str, cmd: str, timeout: int = 120) -> str:
    """Run a shell command on the device and return its output. Useful for
    servers and headless machines."""
    r = requests.post(f"{_base(name)}/shell", headers=_headers(name),
                      json={"cmd": cmd, "timeout": timeout}, timeout=timeout + 10)
    if r.status_code != 200:
        return f"Error: {r.json().get('error', r.text)}"
    o = r.json()
    return (f"[exit {o['returncode']}]\n"
            f"--- stdout ---\n{o['stdout']}\n--- stderr ---\n{o['stderr']}")


# ---- multi-device broadcast -------------------------------------------------
def _targets(devices):
    """Resolve a target list: None/"all" => every paired device."""
    store = _load()
    if not store:
        return []
    if devices in (None, "all", "*", ""):
        return list(store.keys())
    if isinstance(devices, str):
        devices = [d.strip() for d in devices.split(",")]
    return [d for d in devices if d in store]


@mcp.tool()
def broadcast_shell(cmd: str, devices: str = "all", timeout: int = 120) -> str:
    """Run the SAME shell command on several devices at once (default: every
    paired device) and return each one's output. Use for fleet tasks like
    "update all my machines". Pass devices as "all" or a comma-separated list."""
    names = _targets(devices)
    if not names:
        return "No paired devices to target."
    results = {}

    def one(n):
        try:
            r = requests.post(f"{_base(n)}/shell", headers=_headers(n),
                              json={"cmd": cmd, "timeout": timeout},
                              timeout=timeout + 10)
            o = r.json()
            results[n] = (f"[exit {o.get('returncode')}]\n{o.get('stdout', '')}"
                          f"{o.get('stderr', '')}")
        except Exception as e:
            results[n] = f"[error] {e}"

    threads = [threading.Thread(target=one, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return "\n\n".join(f"===== {n} =====\n{out}" for n, out in results.items())


@mcp.tool()
def broadcast_screenshot(devices: str = "all") -> str:
    """Capture a screenshot from several devices at once (default: all paired)
    as PHOTO PROOF of their state. Returns one base64 PNG per device so Claude
    can show/compare them. Run connect() on each device first for the banner."""
    names = _targets(devices)
    if not names:
        return "No paired devices to target."
    shots = {}

    def one(n):
        try:
            r = requests.get(f"{_base(n)}/screenshot", headers=_headers(n), timeout=15)
            shots[n] = r.json().get("png_base64", f"[error] {r.text}")
        except Exception as e:
            shots[n] = f"[error] {e}"

    threads = [threading.Thread(target=one, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Return a JSON map device -> base64 PNG; Claude renders each as an image.
    return json.dumps(shots)


@mcp.tool()
def broadcast_connect(devices: str = "all") -> str:
    """Start controlling several devices at once (shows the banner on each) and
    return the live-view URL for every one, so the user can watch them all as
    VIDEO PROOF. Default: all paired devices."""
    names = _targets(devices)
    if not names:
        return "No paired devices to target."
    lines = []
    for n in names:
        try:
            requests.post(f"{_base(n)}/connect", headers=_headers(n), timeout=10)
            d = _device(n)
            lines.append(f"{n}: http://{d['host']}:{d['port']}/stream")
        except Exception as e:
            lines.append(f"{n}: [error] {e}")
    return ("Connected. Open these URLs in a browser to watch each device "
            "live:\n" + "\n".join(lines))


@mcp.tool()
def broadcast_disconnect(devices: str = "all") -> str:
    """Stop controlling several devices at once and hide their banners."""
    names = _targets(devices)
    for n in names:
        try:
            requests.post(f"{_base(n)}/disconnect", headers=_headers(n), timeout=10)
        except Exception:
            pass
    return f"Disconnected from {len(names)} device(s)."


if __name__ == "__main__":
    mcp.run()
