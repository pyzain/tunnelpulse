<p align="center">
  <img src="assets/icon.png" width="96" alt="TunnelPulse icon">
</p>

<h1 align="center">TunnelPulse</h1>

<p align="center">
  <b>A minimal, self-healing WireGuard VPN client for Windows, with a modern dashboard.</b>
</p>

<p align="center">
  <a href="https://github.com/pyzain/tunnelpulse/actions/workflows/build.yml"><img src="https://github.com/pyzain/tunnelpulse/actions/workflows/build.yml/badge.svg" alt="Build and test"></a>
  <a href="https://github.com/pyzain/tunnelpulse/releases/latest"><img src="https://img.shields.io/github/v/release/pyzain/tunnelpulse?label=download" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-blue" alt="Windows 10 and 11">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
</p>

---

## Download

### [⬇ Download TunnelPulse for Windows](https://github.com/pyzain/tunnelpulse/releases/latest/download/TunnelPulse-Setup.exe)

That link downloads the latest installer (`TunnelPulse-Setup.exe`) straight away.
Other files, such as a portable version that doesn't need installing, are on the
[Releases page](https://github.com/pyzain/tunnelpulse/releases/latest).

Works on Windows 10 and 11 (64-bit).

## Install in 3 steps

1. **Open the file you downloaded.** You'll usually find it in your **Downloads** folder.
2. **If Windows shows "Windows protected your PC"**, click **More info**, then **Run anyway**.
   This appears because the app isn't code-signed yet (a signing certificate is
   a paid yearly service), so Windows doesn't know the publisher. It doesn't
   mean anything is wrong.
3. **Click Yes** when Windows asks *"Do you want to allow this app to make
   changes?"*, then click **Install**, then **Finish**.

That's it. TunnelPulse is now in your Start Menu.

The installer also takes care of **WireGuard**, the official VPN engine
TunnelPulse uses: if it isn't on your PC yet, setup downloads it from
wireguard.com, checks it's genuinely signed by WireGuard LLC, and installs it
for you.

## Connect for the first time

1. You need a **`.conf` file**: a small text file with a VPN server's address
   and your keys. Don't have one? Get one free from Proton VPN:
   - Make a free account at [account.protonvpn.com](https://account.protonvpn.com/).
   - Go to **Downloads**, then **WireGuard configuration**.
   - Type any name (for example `TunnelPulse`), choose **Windows**, then click
     the first **Create** button (the "best server" one) and **Download**.

   Other options are in [You need a server](#you-need-a-server) below.
   Keep your `.conf` file private: it works like a password.
2. Open TunnelPulse and click **Import .conf**. Pick your file.
3. Click **Connect**. The first time, Windows asks for administrator rights
   (connecting installs a network adapter); click **Yes**. TunnelPulse reopens
   and connects on its own.
4. When the status says **Connected**, check the **Protection** card. It
   compares your public internet address before and after connecting, so
   "Protected" really means your traffic goes through the VPN.

---

## What is a VPN? (In plain English)

Normally, when your computer visits a website, your internet provider (and
the website) can see your real location and your real internet address (your
"IP address").

A **VPN (Virtual Private Network)** creates an encrypted tunnel from your
computer to another computer somewhere else, called a **server**. All your
internet traffic goes through that tunnel first. To anyone watching, it looks
like your traffic comes from the server, not from you, so websites see the
server's location and address instead of yours.

**Important:** a VPN always has two ends: a client (this app, on your computer)
and a server (a separate computer your traffic exits from). There's no such
thing as a VPN that works with just a client.

## You need a server

TunnelPulse is the client only. To connect, it needs a WireGuard server, which
you set up once. There are two options:

1. **Host it yourself.** Rent a small cloud server (a "VPS") and install
   WireGuard on it. Some providers, such as Oracle Cloud, have a free tier
   that's enough. At the end you get a `.conf` file to import into TunnelPulse.
2. **Use a WireGuard-compatible VPN provider.** Many providers give you a
   ready-made `.conf` file, with no server setup. Proton VPN does this on its
   free plan too: sign in at account.protonvpn.com, open **Downloads →
   WireGuard configuration**, create one and download it. That's the quickest
   way to try TunnelPulse.

TunnelPulse doesn't include, host or charge for a server. It just needs a
`.conf` file that points at one.

## What makes TunnelPulse different

Most VPN apps on GitHub either need a long, technical setup, or hide how they
actually work. TunnelPulse was built with two goals:

- **Modern and minimal:** one installer, one window, one dashboard, and no
  confusing settings screens.
- **Actually trustworthy:** instead of reinventing encryption (a common and
  dangerous mistake), TunnelPulse drives the official, independently audited
  **WireGuard** software that most commercial VPNs are built on. TunnelPulse's
  own code is the "brain": it decides when to reconnect, checks for problems,
  and shows you what's happening. WireGuard handles the encrypted tunnel itself.

## Features

- ✅ **One-click connect:** import a `.conf` file once, then just click Connect.
- ✅ **Self-healing:** if the tunnel drops, TunnelPulse brings it back on its
  own, waiting a little longer between tries and giving up after 8, so it never
  retries forever.
- ✅ **Real proof it's working:** many VPN apps just show a green tick.
  TunnelPulse checks your actual public internet address before and after
  connecting, so the Protection card tells you the truth.
- ✅ **Tells you when the server isn't answering:** if there's no reply from the
  server within 30 seconds (wrong address, wrong key, server down), the status
  changes to *Degraded* with a plain explanation, instead of staying green.
- ✅ **Kill switch:** if the tunnel drops unexpectedly, your normal internet is
  blocked too, instead of quietly falling back to your unprotected connection.
  TunnelPulse uses WireGuard's built-in kill switch, which switches on for
  profiles that send all traffic through the tunnel.
- ✅ **No surprises after a restart:** tunnels only run while TunnelPulse opens
  them. They don't start by themselves when Windows boots, so a restarted PC
  never ends up offline behind a tunnel nothing is watching.
- ✅ **DNS leak check:** confirms your DNS lookups (which turn website names
  into addresses) go through the tunnel too.
- ✅ **Adaptive link tuning:** one click measures your current network (office
  Wi-Fi, home broadband, mobile data...) and saves the best tunnel packet size
  (MTU) to your profile, so the VPN adds as little overhead as possible.
- ✅ **Dark and light mode:** follows Windows by default, with a toggle.
- ✅ **Live dashboard:** download and upload speed, data used this session,
  connection time, and a live speed graph.

## Updating

When a new version is out, TunnelPulse shows an **Update** button at the bottom
right of the window. Click it, then run the `TunnelPulse-Setup.exe` that
downloads. It installs over your current version and keeps your profiles, so
there's no need to uninstall first. (Downloading the setup from this page and
running it does exactly the same.)

## Uninstalling

Open **Settings → Apps**, find **TunnelPulse**, and click **Uninstall**.

The uninstaller closes the app and removes any TunnelPulse tunnel that's still
running, so nothing is left routing your traffic. It asks whether to delete
your saved profiles too (keep them if you might reinstall).

WireGuard is a separate app and stays installed. Uninstall it the same way if
you no longer need it.

## Is it safe?

- The full source code is right here, and every release is built from it
  automatically on GitHub's servers (see the **Actions** tab), not on someone's
  personal PC.
- Each release lists SHA-256 checksums (`SHA256SUMS.txt`), so you can confirm
  your download wasn't altered.
- The installer only downloads WireGuard from `download.wireguard.com`, and only
  runs it after Windows confirms it's signed by WireGuard LLC.
- The app only asks for administrator rights when you connect, not every time
  it opens.
- Each time it opens, the app asks GitHub once whether there's a newer version.
  Nothing about you or your connection is sent.

---

## For developers

### How it works

TunnelPulse is two Python files, on purpose:

| File | Responsibility |
|---|---|
| `tunnel_pulse_core.py` | All the logic: profiles, starting and stopping tunnels through the official WireGuard binaries, health checks and self-healing, kill switch, DNS leak check, bandwidth monitoring, MTU probing. No GUI code, so it can be tested on its own. |
| `tunnel_pulse_gui.py` | The window (CustomTkinter). Slow work (connecting, checking your IP) runs on background threads and reports back through a queue, so the window never freezes and widgets are only ever touched from the main thread. |

### Project structure

```
tunnelpulse/
├── tunnel_pulse_gui.py         ← the app window, and the PyInstaller entry point
├── tunnel_pulse_core.py        ← all VPN logic (APP_VERSION lives here)
├── build_tunnelpulse.ps1       ← builds the app, a portable zip and the installer
├── RUN_BUILD.bat               ← double-click to run the build script
├── installer/
│   └── TunnelPulse.iss         ← the installer (Inno Setup)
├── assets/                     ← icon and installer artwork (+ the script that draws them)
├── tests/
│   ├── test_core.py            ← unit tests, run on any OS
│   └── windows_e2e.py          ← real WireGuard tunnel test, run on Windows by CI
├── .github/workflows/build.yml ← build, install and test on every push; release on tags
├── requirements.txt
├── LICENSE
└── README.md
```

### Get the source code

Click the green **Code** button near the top of this page, then **Download
ZIP**, and extract it (right-click → **Extract All...**). Or, with Git:

```powershell
git clone https://github.com/pyzain/tunnelpulse.git
```

### Run from source

You need **Python 3.11 or newer** ([python.org/downloads](https://www.python.org/downloads/);
tick **"Add python.exe to PATH"** during setup) and
[WireGuard for Windows](https://www.wireguard.com/install/). In the project
folder:

```powershell
pip install -r requirements.txt
python tunnel_pulse_gui.py
```

### Build the installer yourself

Double-click **`RUN_BUILD.bat`**. It sets up its own Python environment,
installs what it needs (including Inno Setup, through winget, the first time),
and builds into `dist\`:

| Output | What it is |
|---|---|
| `TunnelPulse-Setup.exe` | the installer |
| `TunnelPulse-Portable.zip` | the app folder, zipped |
| `TunnelPulse\` | the app folder itself |

A full log of every build is saved next to the script.

### Tests and CI

```powershell
pip install pytest
python -m pytest tests
```

On every push, GitHub Actions builds everything on a clean Windows machine and
then tests it for real:

1. runs the unit tests
2. installs with `TunnelPulse-Setup.exe` (which has to download, verify and
   install WireGuard), then installs again over it
3. starts the installed app and checks its log for errors
4. **connects to a real WireGuard server**: a second tunnel on the same machine
   acts as the server. The test checks the handshake, kills the tunnel behind
   the app's back to prove self-healing, disconnects, and points the app at a
   dead server to check the warning appears
5. uninstalls, and checks nothing is left behind, including a tunnel left
   running on purpose

### Releasing a new version

1. Change `APP_VERSION` in `tunnel_pulse_core.py` (for example to `"1.1.0"`) and push.
2. Tag the commit with the same version and push the tag:

   ```powershell
   git tag v1.1.0
   git push origin v1.1.0
   ```

The workflow then builds, tests and publishes the release with the installer
attached. The download link at the top of this page always points to the
newest release.

---

## Design notes and known limitations

- **No homemade encryption.** TunnelPulse never implements its own
  cryptography. It leaves that entirely to the official WireGuard components.
- **Reconnects are capped.** If the tunnel keeps failing, TunnelPulse gives up
  after 8 attempts rather than retrying in the background forever.
- **The kill switch only covers full-tunnel profiles.** WireGuard's kill switch
  applies when a profile routes *all* traffic (`AllowedIPs` contains
  `0.0.0.0/0`). For a profile that only routes some traffic, TunnelPulse says
  the kill switch is off rather than pretending otherwise.
- **Closing the window disconnects.** TunnelPulse doesn't keep running in the
  system tray.
- **Profiles are stored as plain files** in `%LOCALAPPDATA%\TunnelPulse`, the
  same way a `.conf` file sits on disk. That includes your private key, and
  anything running under your Windows account can read it. Encrypting them
  (with Windows DPAPI, as the official WireGuard app does) is a planned
  improvement.
- **"Optimize link" needs the server to answer ping.** If it doesn't,
  TunnelPulse says it couldn't measure the link instead of guessing.
- **A VPN can't be faster than your connection.** Encryption and the extra hop
  always cost a little. "Optimize link" keeps that cost as small as possible
  for your current network.

## License

TunnelPulse is licensed under the [MIT License](LICENSE). WireGuard is a
separate project with its own license; TunnelPulse uses it but doesn't include
or redistribute it (the installer downloads it from wireguard.com).

## Author

**Zain Raza**
GitHub: [@pyzain](https://github.com/pyzain)

Copyright © 2026 Zain Raza
