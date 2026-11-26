## Install

1. Download **TunnelPulse-Setup.exe** below.
2. Open it. Windows may say *"Windows protected your PC"*: click **More info**, then **Run anyway**. (The app isn't code-signed yet, so Windows doesn't recognise the publisher.)
3. Click **Yes** when Windows asks to allow changes, then **Install**.

Setup also downloads and installs **WireGuard** from wireguard.com if it isn't on your PC yet, after checking it's genuinely signed by WireGuard LLC.

You'll need a WireGuard `.conf` file from your VPN server or provider to connect. See the [README](https://github.com/pyzain/tunnelpulse#readme).

## Files

| File | What it is |
|---|---|
| `TunnelPulse-Setup.exe` | The installer (recommended) |
| `TunnelPulse-Portable.zip` | The app without installing. Needs WireGuard installed separately. |
| `SHA256SUMS.txt` | Checksums, to confirm your download isn't damaged or altered |

Every release is built and tested automatically on a clean Windows machine, including a real WireGuard connection. See the **Actions** tab.
