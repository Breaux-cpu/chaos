# 🌀 chaos

**The brain half of a two-device pentest platform.** Point a camera at
something, run a full recon/exploitation pass, and hand the result to a
Flipper Zero over the same USB cable — all from a $44 Linux+MCU board that
also happens to already have nmap, nikto, gobuster, sqlmap, and hydra sitting
on it.

Most Flipper Zero projects stop at "custom firmware with a nicer menu."
Nothing else pairs a Flipper-class handheld with a real vision-and-LLM
companion that can look at a device, run a full pentest pass against it, and
push the result back to a screen in your hand — because nothing else has
needed to build that bridge. `chaos` builds it: this repo is the AI/vision
side, running on an **Arduino UNO Q**,
[`flipper-apps`](https://github.com/Breaux-cpu/flipper-apps) is the
on-device display side, and the two talk to each other over plain USB
serial today, verified working, not a roadmap slide.

It's a QR/barcode scanner using the `camera_code_detection` Brick, an
authorized-use pentest toolkit wrapping the six security tools already
installed on this board, a live status mirror on the MCU's LED matrix
(scanning / match / alert), a web dashboard on port 7000, and a bridge that
pushes every scan and every job result straight to a connected Flipper's
screen.

**Authorized use only.** This app can trigger real network scans, credential
brute-forcing, and traffic capture against real hosts. Only point it at
systems you own or are explicitly authorized to test. See
[CONTRIBUTING.md](CONTRIBUTING.md) for what that means for contributions.

## Pentest toolkit

`python/pentest.py` is a standalone module (no dependency on the Arduino app
framework) wrapping eight tools that ship on this board:

| Tool | Use | Notes |
|---|---|---|
| `nmap` | Host discovery / port / service scanning | Profiles: discovery, quick, version, full |
| `nikto` | Web server vulnerability scan | Target must be a URL |
| `gobuster` | Directory/file enumeration | Uses `python/wordlists/common-paths.txt` |
| `sqlmap` | SQL injection testing | `--batch --level=1 --risk=1` — low-noise defaults |
| `hydra` | Credential brute-forcing | ssh/ftp/http-get only, `python/wordlists/{users,passwords}.txt`, stops at first hit |
| `tcpdump` | Packet capture | Writes to `captures/*.pcap`, capped at 120s |
| `wifi_scan` | Passive WiFi recon (aircrack-ng suite) | Enables monitor mode, records nearby APs/clients, restores managed mode. **Interrupts WiFi on that interface for the scan's duration** — if you're connected to this board over the same WiFi link, expect to get dropped until it finishes. |
| `wifi_deauth` | Deauth an AP's client(s) | Needs an interface already in monitor mode (run `wifi_scan` or `airmon-ng start` by hand first). BSSID and client MAC are validated as strict `AA:BB:CC:DD:EE:FF` — blank client MAC deauths everyone on that AP. |

Every target is validated against a strict allow-pattern before it's placed
in a subprocess argv list — commands never go through a shell, so there's no
injection surface. There is **no allowlist of permitted hosts**; scope
enforcement is on you, the operator.

**`tcpdump` and the WiFi tools need a one-time capability grant** — the app
runs as an unprivileged user, so captures/monitor-mode fail with a
permission error until you run:

```bash
sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/tcpdump
sudo setcap cap_net_raw,cap_net_admin=eip /usr/sbin/airmon-ng
sudo setcap cap_net_raw,cap_net_admin=eip /usr/sbin/airodump-ng
sudo setcap cap_net_raw,cap_net_admin=eip /usr/sbin/aireplay-ng
```

The aircrack-ng grants are **unverified** — `airmon-ng` shells out to `iw`/
`ip` internally to reconfigure the interface, and whether its own
capabilities propagate to those child processes depends on the kernel/driver
combination. If a `wifi_scan` job errors out with a permission problem even
after the `setcap` calls above, that's the likely cause — file an issue with
the exact error, it's a known open question, not a mystery to debug from
scratch.

**The dashboard has no authentication by default.** Anyone who can reach
`http://<board-ip>:7000` can trigger scans. Set `CHAOS_PENTEST_TOKEN` in
Brick Configuration to require a shared token on every pentest action. Port
7000 is firewalled to `tailscale0` at the network level on the reference
Arduino UNO Q deployment — check your own host's firewall if you're running
this elsewhere.

## Flipper Zero bridge

`python/flipper_bridge.py` pushes a one-line status update to a connected
Flipper Zero running the companion
[`chaos_relay`](https://github.com/Breaux-cpu/flipper-apps) app, over the
same USB cable — no BLE, no pairing. Fires automatically on every QR/barcode
scan and every pentest job completion/failure. Best-effort: a missing or
busy Flipper never blocks chaos itself. Requires `pyserial`
(`python/requirements.txt`) and `/dev/flipper` (or another stable path to
the device) to be reachable from wherever chaos actually runs.

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

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — including a straightforward path
to adding a new pentest tool wrapper. Please read
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) too.

## Support this project

This is a solo build, in the open, as it happens — the commit history is the
real build log. If you want to back it:

- **⭐ Star the repo.** Sounds small; it's the #1 thing that gets a project
  in front of the next contributor, since it's most of what GitHub's
  discovery surfaces run on.
- **Try it and open issues.** A precise bug report or a "here's where I got
  stuck" from a real run is worth more than a compliment.
- **Send a PR.** [CONTRIBUTING.md](CONTRIBUTING.md) has a scoped first task
  (add a new pentest tool wrapper) that doesn't require reading the whole
  codebase first.
- **[💜 Sponsor on GitHub](https://github.com/sponsors/Breaux-cpu)** — funds
  the actual hardware this project depends on (a Flipper Zero, USB
  peripherals, eventually a second board for Track A). If that link 404s,
  GitHub Sponsors isn't enabled on the account yet — starring/sharing still
  helps just as much in the meantime.
- **Share it** with anyone who'd find the Arduino UNO Q ↔ Flipper bridge
  idea interesting — that's the piece nobody else has built yet.

## License

[MIT](LICENSE)
