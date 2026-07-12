# 🌀 chaos

AI-augmented recon companion for jessy — vision ID, pentest toolkit, remote control.

This is the Phase 1 slice of the two-track build plan: a QR/barcode scanner
using the `camera_code_detection` Brick, an authorized-use pentest toolkit
wrapping the tools already installed on this board, a live status mirror on
the MCU's LED matrix (scanning / match / alert), and a web dashboard on port
7000 for both.

**Authorized use only.** This app can trigger real network scans, credential
brute-forcing, and traffic capture against real hosts. Only point it at
systems you own or are explicitly authorized to test.

## Pentest toolkit

`python/pentest.py` is a standalone module (no dependency on the Arduino app
framework) wrapping six tools that ship on this board:

| Tool | Use | Notes |
|---|---|---|
| `nmap` | Host discovery / port / service scanning | Profiles: discovery, quick, version, full |
| `nikto` | Web server vulnerability scan | Target must be a URL |
| `gobuster` | Directory/file enumeration | Uses `python/wordlists/common-paths.txt` |
| `sqlmap` | SQL injection testing | `--batch --level=1 --risk=1` — low-noise defaults |
| `hydra` | Credential brute-forcing | ssh/ftp/http-get only, `python/wordlists/{users,passwords}.txt`, stops at first hit |
| `tcpdump` | Packet capture | Writes to `captures/*.pcap`, capped at 120s |

Every target is validated against a strict allow-pattern before it's placed
in a subprocess argv list — commands never go through a shell, so there's no
injection surface. There is **no allowlist of permitted hosts**; scope
enforcement is on you, the operator.

**`tcpdump` needs a one-time capability grant** — the app runs as an
unprivileged user, so captures fail with a permission error until you run:

```bash
sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/tcpdump
```

**The dashboard has no authentication by default.** Anyone who can reach
`http://<board-ip>:7000` can trigger scans. Set `CHAOS_PENTEST_TOKEN` in
Brick Configuration to require a shared token on every pentest action, and
if jessy is reachable beyond your LAN (e.g. its public IPv6), firewall port
7000 to trusted interfaces the same way you did for VNC.

## Run it

```bash
arduino-app-cli app start ~/ArduinoApps/chaos
arduino-app-cli app logs ~/ArduinoApps/chaos --follow
```

Then open `http://<board-ip>:7000` for the dashboard, or `arduino-app-cli monitor`
for MCU-side serial output. QR/barcode scanning requires a USB webcam.

## What's next

- Object/image classification for device ID beyond QR/barcode
- `dbstorage_sqlstore` for persistent scan/job history
- Telegram bot for remote alerts
- BLE bridge to a Flipper Zero–class companion device
