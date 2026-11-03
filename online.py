"""
online.py - give the laptop's app a secure https link for phones (double-click run_online.bat).

WHY: phones need https to use their microphone. Cloudflare's free "quick tunnel" (no account) gives
this laptop's app a link like https://some-words.trycloudflare.com. Phones scan the QR code on the
dashboard, and their tickets land on this laptop's dashboard live. The link changes each time.
Keep this window open during the demo; close it (or press Ctrl+C) to take the link down.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent


def find_cloudflared() -> str | None:
    for path in (shutil.which("cloudflared"), r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                 r"C:\Program Files\cloudflared\cloudflared.exe"):
        if path and Path(path).exists():
            return path
    return None


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port() -> int:
    # Always run OUR OWN copy of the app. Borrowing one started elsewhere (another window, another
    # program) means the link dies when that copy is closed - which is exactly what happened on Nov 3.
    return next(p for p in range(8501, 8520) if not port_in_use(p))


def start_app(port: int, url: str) -> subprocess.Popen:
    env = dict(os.environ, APP_URL=url)               # so the QR code points at the link, even on localhost
    app = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
                            "--server.port", str(port)], cwd=ROOT, env=env)
    for _ in range(60):
        if port_in_use(port):
            break
        time.sleep(1)
    return app


def keep_reading(stream) -> None:
    # cloudflared keeps printing status lines. If nobody reads them, Windows pauses it once enough
    # pile up - and the link freezes. So read (and ignore) them for as long as it runs.
    for _ in stream:
        pass


def main() -> None:
    cloudflared = find_cloudflared()
    if not cloudflared:
        sys.exit("Cloudflare's tunnel tool is missing. Install it once with:  winget install Cloudflare.cloudflared")

    port = free_port()
    print("Starting the secure link...")
    tunnel = subprocess.Popen([cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = None
    for line in tunnel.stdout:                         # cloudflared prints the link among its startup lines
        found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if found:
            url = found.group(0)
            break
    if not url:
        sys.exit("Couldn't get a link from Cloudflare - check the internet connection and try again.")
    threading.Thread(target=keep_reading, args=(tunnel.stdout,), daemon=True).start()

    print("Starting the app...")
    app = start_app(port, url)

    print("\n" + "=" * 70)
    print(f"  ONLINE LINK (phones):  {url}")
    print(f"  Staff Dashboard:       {url}/staff_dashboard   (opening it now)")
    print("  Keep this window open during the demo. Close it to take the link down.")
    print("=" * 70 + "\n")
    time.sleep(3)                                      # give Cloudflare a moment before the first visit
    webbrowser.open(f"{url}/staff_dashboard")
    try:
        while True:                                    # watch both; restart the app if it ever stops
            if tunnel.poll() is not None:
                print("\n!! The secure link stopped. Close this window and double-click run_online.bat again.")
                break
            if app.poll() is not None:
                print("\n!! The app stopped - restarting it (same link)...")
                app = start_app(port, url)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        app.terminate()


if __name__ == "__main__":
    main()
