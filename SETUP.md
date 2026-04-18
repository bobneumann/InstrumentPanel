# Control Room — Setup Guide

> **Living document.** Written for a church AV/IT context. Covers the full path
> from a fresh machine to green dots on screen.

---

## What you need before you start

- **Windows 10/11** (Mac/Linux work but this guide assumes Windows)
- **Python 3.11 or newer** — [python.org](https://python.org). During install, check "Add Python to PATH."
- **WSL** (Windows Subsystem for Linux) — required only if you're monitoring SNMP devices (switches, UPS, printers). Skip if you're TCP/SSH only.
- **Git** — [git-scm.com](https://git-scm.com)

---

## Step 1 — Clone the repo

Open a terminal (PowerShell or Command Prompt) and run:

```bat
git clone https://github.com/bobneumann/ControlRoom.git
cd ControlRoom
```

---

## Step 2 — Install Python dependencies

```bat
pip install -r requirements.txt
```

This installs PySide6 (the GUI), paramiko (SSH), FastAPI and uvicorn (daemon), and a few others.
If `pip` isn't found, try `py -m pip install -r requirements.txt`.

---

## Step 3 — SNMP support (skip if not monitoring switches/UPS/printers)

SNMP polling uses the Linux `snmpget` binary via WSL. If WSL isn't installed:

```powershell
# In PowerShell as Administrator
wsl --install
```

Restart, then open an Ubuntu terminal and run:

```bash
sudo apt update && sudo apt install snmp -y
```

Verify it works:

```bash
snmpget --version
```

---

## Step 4 — Create an SSH key for monitoring

Control Room SSHes into devices to read CPU, RAM, disk, and load. It uses a dedicated key
so you're not sharing your personal key.

```bat
ssh-keygen -t ed25519 -f C:\Users\YourName\.ssh\panel.key
```

Press Enter twice (no passphrase). This creates two files:
- `panel.key` — private key (stays on this machine)
- `panel.key.pub` — public key (goes on every monitored device)

**For each Linux device you want to monitor:**
```bash
ssh-copy-id -i C:\Users\YourName\.ssh\panel.key.pub user@device-ip
```

**For Windows devices**, append the contents of `panel.key.pub` to
`C:\Users\username\.ssh\authorized_keys` on the target machine.

Verify with:
```bat
ssh -i C:\Users\YourName\.ssh\panel.key user@device-ip
```

You should get a shell without being asked for a password.

---

## Step 5 — Configure your devices (hosts.json)

```bat
copy hosts.json.example hosts.json
```

Open `hosts.json` in a text editor. Each entry defines one monitored device.
The `key` is a short internal ID (no spaces). The `label` is what shows in the UI.

**Four device types:**

### TCP — reachability + latency
Use for any device you just want to ping on a port: video switchers, relay panels, cameras.
```json
{
  "key": "atem_switcher",
  "label": "ATEM 4 M/E",
  "type": "tcp",
  "poll_interval": 10,
  "collector": {
    "host": "192.168.1.21",
    "port": 9910,
    "timeout": 5,
    "health_rules": [
      { "metric": "latency_ms", "warn_above": 50, "error_above": 200 }
    ]
  }
}
```

### SSH — CPU, RAM, disk, load
Use for Linux and Windows machines where you can install an SSH server.
```json
{
  "key": "streaming_pc",
  "label": "Streaming PC",
  "type": "ssh",
  "poll_interval": 5,
  "collector": {
    "host": "192.168.1.51",
    "port": 22,
    "user": "avtech",
    "key": "C:/Users/YourName/.ssh/panel.key",
    "os": "windows",
    "health_rules": [
      { "metric": "cpu",  "warn_above": 65, "error_above": 85 },
      { "metric": "ram",  "warn_above": 75, "error_above": 90 },
      { "metric": "disk", "warn_above": 85, "error_above": 95 }
    ]
  }
}
```
Set `"os"` to `"linux"` or `"windows"` to match the target.

### SNMP — switches, UPS, printers
Use for network gear and power equipment that speaks SNMP v2c.
```json
{
  "key": "ups_main",
  "label": "APC Smart-UPS",
  "type": "snmp",
  "poll_interval": 30,
  "collector": {
    "host": "192.168.1.60",
    "community": "public",
    "snmpget_path": "wsl snmpget",
    "oids": {
      "battery_pct": "1.3.6.1.4.1.318.1.1.1.2.2.1.0",
      "load_pct":    "1.3.6.1.4.1.318.1.1.1.4.2.3.0",
      "runtime_min": "1.3.6.1.4.1.318.1.1.1.2.2.3.0"
    },
    "health_rules": [
      { "metric": "battery_pct", "warn_below": 50, "error_below": 25 },
      { "metric": "load_pct",    "warn_above": 70, "error_above": 90 }
    ]
  }
}
```

See `hosts.json.example` for an HTTP example and the full field reference.

---

## Step 6 — Set up your floor plan (ops_board.json)

```bat
copy ops_board.json.example ops_board.json
```

Open `ops_board.json`. Two things to configure:

**Background image** — set this to your floor plan file:
```json
"background": "C:/path/to/your/floorplan.png"
```
Any PNG or JPG works. A simple floor plan sketch is fine. Leave it blank (`""`) to use a plain background.

**Entity positions** — `x` and `y` are fractions of the image width/height, from the top-left corner.
`x: 0.5, y: 0.5` is dead center. You'll drag these into place from inside the app — the JSON
is just the starting point.

Each entity's `key` must match a key in `hosts.json`.

Available icons: `generic`, `server`, `switch`, `camera`, `speaker`, `display`, `controller`, `nas`

---

## Step 7 — First launch

```bat
run_designer.bat
```

The app opens. Switch to **Ops Board** view using the toolbar button.

Expect most dots to be **amber** (connecting) at first. Within 10–30 seconds, reachable devices
should turn **green**. Unreachable ones will turn **red** after their timeout.

If everything stays amber for more than a minute, check:
- IP address correct in hosts.json?
- Device is on and reachable? (`ping 192.168.1.xx` from your terminal)
- SSH: key deployed to the device? (`ssh -i panel.key user@ip` works manually?)
- SNMP: correct community string? (`wsl snmpget -v2c -c public 192.168.1.xx 1.3.6.1.2.1.1.1.0`)

---

## Step 8 — Place entities on the floor plan

In the Ops Board sidebar, drag entities from the staging tray onto the floor plan, or click and drag
placed entities to reposition them. The layout saves automatically when you exit edit mode (**E** key).

---

## Step 9 — Tune health thresholds

Default thresholds are conservative. Once devices are green and reporting real data:

1. Click any entity on the Ops Board
2. Choose **Definition…** from the context menu
3. Adjust `warn` and `error` thresholds to match what you actually see in normal operation

Changes take effect immediately and are saved to hosts.json.

---

## Step 10 — Create crew slates

A slate is a named layout — a specific combination of Instrument Panel + Ops Board — saved as a profile.
You might have: Master (wall display), Sound, Lighting, Video.

1. Open the **Slate Manager** from the toolbar
2. Click **New** and give it a name
3. Switch to that slate, arrange the Ops Board and Instrument Panel for that crew
4. Repeat for each crew

Each slate stores its own ops board entity set and its own instrument panel gauge layout.
The Master slate is what goes on the wall.

---

## Step 11 — Production: run the daemon (optional but recommended)

In single-machine mode (what you've been doing), the display app polls devices itself.
For a production install with multiple displays, one machine runs a headless daemon that
polls everything, and each display connects to it.

**On the always-on machine:**
```bat
run_daemon.bat
```

**On each display machine:**
```bat
py designer.py --slate "Sound" --daemon http://192.168.1.X:8765
```

**Wall kiosk (full-screen, read-only):**
```bat
py designer.py --slate "Master" --daemon http://192.168.1.X:8765 --kiosk
```

To start the daemon automatically at boot, use Windows Task Scheduler:
- Trigger: At startup
- Action: `py C:\path\to\ControlRoom\daemon.py`
- Check: "Run whether user is logged on or not"

See **INSTALL.md** for deeper coverage of daemon persistence, SNMP, baselines, and troubleshooting.

---

## Quick reference

| File | What it is |
|------|-----------|
| `hosts.json` | Your devices — IPs, credentials, health rules |
| `ops_board.json` | Entity positions on the floor plan |
| `slates.json` | Named slate index (auto-created on first run) |
| `layout.json` | Main instrument panel layout |
| `layout_{key}.json` | Per-device detail layout (auto-generated) |

All of the above are gitignored — they contain your site-specific data and never get committed.
