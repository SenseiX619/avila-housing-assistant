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
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
PORT = 8501


def find_cloudflared() -> str | None:
    for path in (shutil.which("cloudflared"), r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                 r"C:\Program Files\cloudflared\cloudflared.exe"):
        if path and Path(path).exists():
            return path
    return None


def app_running() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main() -> None:
    cloudflared = find_cloudflared()
    if not cloudflared:
        sys.exit("Cloudflare's tunnel tool is missing. Install it once with:  winget install Cloudflare.cloudflared")

    print("Starting the secure link...")
    tunnel = subprocess.Popen([cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{PORT}"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = None
    for line in tunnel.stdout:                         # cloudflared prints the link among its startup lines
        found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if found:
            url = found.group(0)
            break
    if not url:
        sys.exit("Couldn't get a link from Cloudflare - check the internet connection and try again.")

    app = None
    if not app_running():
        print("Starting the app...")
        env = dict(os.environ, APP_URL=url)           # so the QR code points at the link, even on localhost
        app = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
                                "--server.port", str(PORT)], cwd=ROOT, env=env)
        for _ in range(60):
            if app_running():
                break
            time.sleep(1)

    print("\n" + "=" * 70)
    print(f"  ONLINE LINK (phones):  {url}")
    print(f"  Staff Dashboard:       {url}/staff_dashboard   (opening it now)")
    print("  Keep this window open during the demo. Close it to take the link down.")
    print("=" * 70 + "\n")
    time.sleep(3)                                      # give Cloudflare a moment before the first visit
    webbrowser.open(f"{url}/staff_dashboard")
    try:
        tunnel.wait()
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        if app:
            app.terminate()


if __name__ == "__main__":
    main()
