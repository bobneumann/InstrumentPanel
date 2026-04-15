# InstrumentPanel

Skeuomorphic live machine metrics dashboard built with Python + PySide6.
Displays CPU, RAM, disk, network, and per-core usage as themed analog gauges —
no agents installed on monitored machines.

![WWII Cockpit theme with live gauges]

## Features

- **Pull-only, agentless** — monitors remote Linux/Windows hosts over SSH
- **Live animation** — 60 fps needle movement between 1–2 s poll intervals
- **Themes** — WWII Cockpit, F1 Racing (more planned)
- **Interactive designer** — drag gauges, assign sources, resize grid, save layouts
- **Cross-platform** — runs on Windows and Linux

## Quick Start

### 1. Install dependencies

```
pip install -r requirements.txt
```

Remote hosts also need `psutil` installed:
- Linux: `sudo apt install python3-psutil`
- Windows: `pip install psutil`

### 2. Configure remote hosts (optional)

```
copy hosts.json.example hosts.json
```

Edit `hosts.json` with your server addresses, SSH usernames, and key paths.
SSH key-based authentication is strongly recommended over passwords.

### 3. Run the designer

**Windows:**
```
run_designer.bat
```

**Linux / WSL:**
```
python3 designer.py
```

Press **E** to toggle edit mode. In edit mode: add/remove gauges, drag to rearrange,
assign data sources, switch themes, resize the grid.

## Files

| File | Purpose |
|------|---------|
| `designer.py` | Main application — interactive layout designer |
| `gauge.py` | `Gauge` widget, `GaugeConfig`, `GaugeTheme`, theme factories |
| `datasources.py` | Local metric sources (psutil) |
| `remote_host.py` | SSH polling thread per remote host |
| `host_registry.py` | Loads `hosts.json`, registers sources into designer |
| `panel.py` | Standalone live panel (no designer UI) |
| `hosts.json.example` | Template — copy to `hosts.json` and fill in your servers |

## Layout persistence

Your gauge layout is saved to `layout.json` automatically when you exit edit mode.
This file is user-specific and excluded from version control.

## Remote host requirements

| | Linux | Windows |
|--|-------|---------|
| Python | `python3` | `python` (3.x) |
| psutil | `sudo apt install python3-psutil` | `pip install psutil` |
| SSH | OpenSSH server | OpenSSH for Windows |

Windows OpenSSH server: `Enable-WindowsOptionalFeature -Online -FeatureName OpenSSH-Server`

## Packaging (PyInstaller)

```
pip install pyinstaller
pyinstaller --onefile --windowed designer.py
```

The resulting `dist/designer.exe` runs standalone — no Python required on target machine.
Copy `hosts.json.example` alongside it; rename to `hosts.json` and configure.

## License

MIT
