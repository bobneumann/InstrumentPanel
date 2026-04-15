"""
Host registry — loads hosts.json, starts RemoteHost instances, and
injects their metric sources into the designer's SOURCE_REGISTRY.

hosts.json format (place next to designer.py):
[
  {
    "name":    "Epic Prod",
    "host":    "phcldc21001",
    "port":    22,
    "user":    "epicadm",
    "key":     "C:/Users/Bob/.ssh/id_rsa",
    "os":      "linux",
    "poll_s":  2.0
  },
  {
    "name":    "App Server",
    "host":    "192.168.1.50",
    "user":    "admin",
    "key":     "C:/Users/Bob/.ssh/id_rsa",
    "os":      "windows",
    "poll_s":  2.0
  }
]

Fields:
  name     Display name shown in gauge picker and labels
  host     Hostname or IP
  port     SSH port (default 22)
  user     SSH username
  key      Path to private key file (recommended)
  password SSH password (alternative to key; not recommended)
  os       "linux" or "windows"  (default "linux")
  poll_s   Poll interval in seconds (default 2.0)

After load(), SOURCE_REGISTRY gains entries like:
  "epic_prod:cpu"     "Epic Prod — CPU"       %
  "epic_prod:ram"     "Epic Prod — RAM"       %
  "epic_prod:disk"    "Epic Prod — Disk"      %
  "epic_prod:net_in"  "Epic Prod — Net In"    MB/s
  "epic_prod:net_out" "Epic Prod — Net Out"   MB/s
  "epic_prod:core_0"  "Epic Prod — Core 0"    %
  ... up to core_7
"""

import os
import json
import logging
from typing import Optional

log = logging.getLogger(__name__)

try:
    from remote_host import RemoteHost, _PARAMIKO_OK
except ImportError:
    RemoteHost  = None
    _PARAMIKO_OK = False

_active_hosts: list = []


def load(hosts_path: str, source_registry: dict) -> list:
    """
    Read hosts.json, create RemoteHost instances, inject sources into
    source_registry, start background threads.

    Returns the list of RemoteHost objects (keep a reference to call
    stop_all() on shutdown).
    """
    global _active_hosts

    if not _PARAMIKO_OK:
        log.warning("paramiko missing — remote hosts not loaded.")
        return []

    if not os.path.exists(hosts_path):
        return []

    try:
        with open(hosts_path) as f:
            configs = json.load(f)
    except Exception as exc:
        log.error("Cannot read %s: %s", hosts_path, exc)
        return []

    hosts = []
    for cfg in configs:
        # Skip comment/example entries
        if cfg.get("_comment"):
            continue
        try:
            h = RemoteHost(
                name     = cfg["name"],
                host     = cfg["host"],
                port     = cfg.get("port", 22),
                user     = cfg["user"],
                key_path = cfg.get("key"),
                password = cfg.get("password"),
                os       = cfg.get("os", "linux"),
                poll_s   = cfg.get("poll_s", 2.0),
            )
            _register(h, source_registry)
            h.start()
            hosts.append(h)
            log.info("Loaded remote host: %s (%s)", h.name, cfg["host"])
        except Exception as exc:
            log.error("Bad host config %s: %s", cfg.get("name", "?"), exc)

    _active_hosts = hosts
    return hosts


def stop_all():
    """Stop all background polling threads.  Call on app exit."""
    for h in _active_hosts:
        h.stop()


# ── internal ──────────────────────────────────────────────────────────────── #

def _key(name: str) -> str:
    """Normalise host name to a SOURCE_REGISTRY key prefix."""
    return name.lower().replace(" ", "_").replace("-", "_")


def _register(host: "RemoteHost", registry: dict):
    """Add all metric sources for one host into source_registry."""
    p = _key(host.name)
    n = host.name

    # Pre-create net-rate callables (stateful; shared across all gauges
    # for this host so they all see the same rate reading)
    net_in_src  = host.net_rate_source("recv")
    net_out_src = host.net_rate_source("sent")

    entries = {
        f"{p}:cpu":      {"label": f"{n} — CPU",         "unit": "%",       "factory": lambda h=host: h.source("cpu")},
        f"{p}:ram":      {"label": f"{n} — RAM",         "unit": "%",       "factory": lambda h=host: h.source("ram")},
        f"{p}:disk":     {"label": f"{n} — Disk",        "unit": "%",       "factory": lambda h=host: h.source("disk")},
        f"{p}:net_in":   {"label": f"{n} — Net In",      "unit": "MB/s",    "factory": lambda s=net_in_src:  (lambda: s())},
        f"{p}:net_out":  {"label": f"{n} — Net Out",     "unit": "MB/s",    "factory": lambda s=net_out_src: (lambda: s())},
        f"{p}:ctx_rate": {"label": f"{n} — CTX SW",      "unit": "× 10 /s", "factory": lambda h=host: h.source("ctx_rate")},
        f"{p}:load1":    {"label": f"{n} — Load Avg",    "unit": "× 0.05",  "factory": lambda h=host: h.source("load1")},
    }
    for i in range(8):
        entries[f"{p}:core_{i}"] = {
            "label":   f"{n} — Core {i}",
            "unit":    "%",
            "factory": lambda h=host, k=f"core_{i}": h.source(k),
        }

    for key, info in entries.items():
        info["group"] = n   # used by picker to group under host name
        registry[key] = info
