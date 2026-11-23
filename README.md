# TunnelPulse

**A minimal, self-healing WireGuard VPN client for Windows, with a modern dashboard.**

---

## Table of Contents

1. [What Is a VPN? (In Plain English)](#what-is-a-vpn-in-plain-english)
2. [What Makes TunnelPulse Different](#what-makes-tunnelpulse-different)
3. [Before You Start: You Need Your Own Server](#before-you-start-you-need-your-own-server)
4. [Features](#features)
5. [How It Works](#how-it-works)
6. [Project Structure](#project-structure)
7. [How to Download This Project](#how-to-download-this-project)
8. [Running It From Source](#running-it-from-source)
9. [Building Your Own .exe](#building-your-own-exe)
10. [Design Notes & Known Limitations](#design-notes--known-limitations)
11. [License](#license)
12. [Author](#author)

---

## What Is a VPN? (In Plain English)

Normally, when your computer visits a website, your internet provider (and
the website) can see your real location and your real internet address (your
"IP address").

A **VPN (Virtual Private Network)** creates an encrypted tunnel from your
computer to another computer somewhere else (called a **server**). All your
internet traffic goes through that tunnel first. To anyone watching, it
looks like your traffic is coming from the server, not from you — so
websites see the server's location and address instead of yours.

**Important:** a VPN always needs two ends — a client (this app, on your
computer) and a server (a separate computer somewhere else that your traffic
exits from). There is no such thing as a VPN that works with just a client
and nothing else.

## What Makes TunnelPulse Different

Most VPN apps on GitHub either come with a long, technical installation
process, or they hide how they actually work. TunnelPulse was built with two
specific goals:

- **Modern and minimal** — one window, one dashboard, no confusing settings
  screens.
- **Actually trustworthy** — instead of reinventing encryption from scratch
  (a common and dangerous mistake), TunnelPulse orchestrates the official,
  independently-audited **WireGuard** software that most commercial VPNs
  are already built on. TunnelPulse's own Python code is the "brain" —
  deciding when to reconnect, checking for problems, and showing you what's
  happening — while WireGuard itself handles the actual encrypted tunnel.

## Before You Start: You Need Your Own Server

TunnelPulse is the client only. To actually connect to anything, you need a
WireGuard server for it to connect to. You have two options:

1. **Self-host it** — rent a small cloud server (a "VPS") and install
   WireGuard on it yourself. Some providers (like Oracle Cloud) offer a
   free-forever tier that's enough for this. You get a `.conf` file at the
   end, which you import into TunnelPulse.
2. **Use a WireGuard-compatible provider** — some paid VPN providers (e.g.
   Mullvad) will hand you a ready-made `.conf` file, no server setup
   required.

Either way, TunnelPulse itself doesn't include, host, or charge for a
server — it just needs a `.conf` file pointing at one.

## Features

- ✅ **One-click connect** — import a `.conf` file once, then just click
  Connect.
- ✅ **Self-healing** — if the tunnel drops, TunnelPulse automatically
  retries on its own (with increasing wait times between tries, capped so
  it never retries forever).
- ✅ **Real proof it's working** — most VPN apps just show a green
  checkmark and hope. TunnelPulse checks your actual public internet
  address before and after connecting, so the "Protection" card tells you
  the truth, not just a status light.
- ✅ **Kill switch** — if the tunnel unexpectedly drops, your regular
  internet connection is blocked too, instead of silently falling back to
  your normal, unprotected connection. TunnelPulse uses WireGuard's own
  built-in kill switch, which switches on automatically for profiles that
  send all traffic through the tunnel, and switches off the moment the
  tunnel is removed — so it can never leave your PC stuck offline.
- ✅ **DNS leak check** — confirms your DNS lookups (which translate
  website names into addresses) are actually going through the tunnel too.
- ✅ **Adaptive link tuning** — one click measures your current network
  (office Wi-Fi, home broadband, mobile data, etc.) and saves the best
  tunnel packet size (MTU) to your profile, so the VPN adds as little
  overhead as possible.
- ✅ **Dark and light mode** — a single toggle, built so that switching
  themes can never leave a button the wrong color (a common bug in
  dashboard-style apps).
- ✅ **Live dashboard** — download/upload speed, session data used,
  connection uptime, and a live speed graph.

## How It Works

TunnelPulse is split into exactly two files, on purpose:

```
tunnel_pulse_core.py   the "brain" — no windows, no buttons, all logic
tunnel_pulse_gui.py    the dashboard you see, plus the compiled entry point
```

| File | Responsibility |
|---|---|
| `tunnel_pulse_core.py` | Manages VPN profiles, starts/stops the tunnel via the official WireGuard binaries, runs the kill switch, checks for DNS leaks, monitors bandwidth, and automatically reconnects if something goes wrong |
| `tunnel_pulse_gui.py` | The window you actually interact with — reads live updates from the core and never freezes, because all the slow work (connecting, checking your IP, etc.) happens in the background |

The two are kept strictly separate: the core file has zero GUI code in it
(so it can be tested on its own), and the GUI file never duplicates any
tunnel logic — it only calls into the core.

## Project Structure

```
tunnelpulse/
├── tunnel_pulse_gui.py      ← Run this to start the app (also the build entry point)
├── tunnel_pulse_core.py     ← All VPN logic — connect, self-heal, verify, monitor
├── build_tunnelpulse.ps1    ← Automated build script (creates the .exe)
├── RUN_BUILD.bat            ← Double-click this to run the build script
├── requirements.txt
├── LICENSE
└── README.md
```

---

## How to Download This Project

If you've never used GitHub before, here's exactly what to click.

1. Go to this repository's page in your browser.
2. Near the top of the file list, click the **green "Code" button**.
3. In the dropdown, click **"Download ZIP"** near the bottom.
4. The `.zip` file downloads to your **Downloads** folder.
5. In **File Explorer**, right-click the downloaded `.zip` → **"Extract
   All..."** → choose a folder → **"Extract"**.
6. You now have a normal folder with the project in it.

## Running It From Source

You'll need **Python 3.11 or newer**. If you don't have it, get it from
[python.org/downloads](https://www.python.org/downloads/) — during setup,
tick **"Add python.exe to PATH"** before clicking Install.

Open the project folder, hold **Shift** and **right-click** an empty area,
choose **"Open PowerShell window here"**, then run:

```powershell
pip install -r requirements.txt
python tunnel_pulse_gui.py
```

The app window will open. You'll also need the official **WireGuard for
Windows** package installed (download it from
[wireguard.com/install](https://www.wireguard.com/install/)) — TunnelPulse
looks for it automatically and will tell you if it can't find it.

## Building Your Own .exe

Want a single folder you can double-click to run, with no Python needed?

1. Open the project folder in File Explorer.
2. **Double-click `RUN_BUILD.bat`.**
3. A window opens and does everything automatically — sets up an isolated
   Python environment, installs everything needed, builds the app, and
   zips it up. This takes a few minutes the first time.
4. When it finishes, a File Explorer window will pop up pointing straight
   at the finished `TunnelPulse-v1.0.0-win64.zip` — that's your
   ready-to-share build.

The build script is self-checking: if something's missing (like Python
itself), it tells you exactly what's wrong instead of just failing
silently. A full log of every build is saved next to the script, in case
anything needs debugging later.

**Note:** the built app expects `wireguard.exe`, `wg.exe`, and `wintun.dll`
to either already be installed on the PC running it, or placed in a `bin\`
folder next to the script before building (which bundles them in). These
are official WireGuard files — download them from
[wireguard.com/install](https://www.wireguard.com/install/); this project
doesn't include or redistribute them itself.

The first time you run an unsigned `.exe` like this, Windows SmartScreen
will show an "unknown publisher" warning — that's expected for any app
without a paid code-signing certificate, not a sign anything's wrong. Click
**"More info"** → **"Run anyway."**

---

## Design Notes & Known Limitations

A few deliberate choices worth knowing about:

- **No homemade encryption.** TunnelPulse never implements its own
  cryptography — it hands that entirely to the official WireGuard
  components, the same trusted implementation most commercial WireGuard
  VPNs are built on.
- **Reconnects are capped, not infinite.** If the tunnel keeps failing,
  TunnelPulse gives up after 8 attempts (with increasing delays between
  each) rather than retrying forever in the background.
- **Kill switch only covers full-tunnel profiles.** WireGuard's built-in
  kill switch applies when a profile routes *all* traffic (AllowedIPs
  contains `0.0.0.0/0`). If you import a profile that only routes some
  traffic, TunnelPulse tells you the kill switch is off rather than
  pretending otherwise.
- **"Optimize link" needs the server to answer ping.** Some servers block
  ping; in that case TunnelPulse says it couldn't measure the link instead
  of guessing a value.
- **A VPN can't make your internet faster than your actual connection** —
  encryption and the extra hop always cost some overhead. TunnelPulse's
  "Optimize link" feature only minimizes that overhead for your current
  network, it doesn't (and can't) beat your raw connection speed.

## License

This project is licensed under the [MIT License](LICENSE) for the
TunnelPulse code itself. WireGuard is a separate, independent project with
its own license — TunnelPulse orchestrates it but does not include or
redistribute its binaries.

## Author

**Zain Raza**
GitHub: [@pyzain](https://github.com/pyzain)

Copyright © 2026 Zain Raza
