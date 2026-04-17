"""
SSH collector — persistent paramiko connection, Python+psutil script on remote.

Refactored from remote_host.py into the standard poll(config, state) interface.
The SSH client is stored in `state` and reused across polls.

config.collector fields:
  host      Hostname or IP
  port      SSH port (default 22)
  user      SSH username
  key       Path to private key file (recommended)
  password  SSH password (alternative to key)
  os        "linux" or "windows" (default "linux")

Metrics returned:
  cpu, ram, disk, net_in (MB/s), net_out (MB/s),
  ctx_rate (Linux only), load1 (Linux only),
  core_0 … core_7

Requires: pip install paramiko psutil (psutil on the *remote* host)
"""

import json
import time
import logging

log = logging.getLogger(__name__)

try:
    import paramiko
    _OK = True
except ImportError:
    _OK = False
    log.warning("paramiko not installed — SSH collector disabled.  "
                "Run: pip install paramiko")


# ── Remote metric scripts ────────────────────────────────────────────────── #

_LINUX_SCRIPT = b"""
import psutil, json
stats_a = psutil.cpu_stats()
cpu     = psutil.cpu_percent(interval=0.2)
stats_b = psutil.cpu_stats()
ctx_per_sec = (stats_b.ctx_switches - stats_a.ctx_switches) / 0.2
n   = psutil.net_io_counters()
mem = psutil.virtual_memory()
try:
    load1 = float(open('/proc/loadavg').read().split()[0])
except Exception:
    load1 = 0.0
print(json.dumps({
    'cpu':      cpu,
    'ram':      mem.percent,
    'disk':     psutil.disk_usage('/').percent,
    'cores':    psutil.cpu_percent(percpu=True, interval=None),
    'net_sent': n.bytes_sent,
    'net_recv': n.bytes_recv,
    'ctx_rate': min(ctx_per_sec / 10.0, 100.0),
    'load1':    min(load1 * 20.0,       100.0),
}))
"""

_WINDOWS_SCRIPT = b"""
import psutil, json
n = psutil.net_io_counters()
psutil.cpu_percent(interval=0.2)
cores = psutil.cpu_percent(percpu=True, interval=None)
print(json.dumps({
    'cpu':      psutil.cpu_percent(interval=None),
    'ram':      psutil.virtual_memory().percent,
    'disk':     psutil.disk_usage('C:\\\\').percent,
    'cores':    cores,
    'net_sent': n.bytes_sent,
    'net_recv': n.bytes_recv,
}))
"""


# ── poll() ───────────────────────────────────────────────────────────────── #

def poll(config: dict, state: dict) -> tuple[dict, dict]:
    if not _OK:
        return {"health": "error", "message": "paramiko not installed", "metrics": {}}, state

    c   = config["collector"]
    ssh = state.get("ssh")

    alive = (
        ssh is not None
        and ssh.get_transport() is not None
        and ssh.get_transport().is_active()
    )

    if not alive:
        ssh = _connect(c)
        if ssh is None:
            return {
                "health":  "error",
                "message": f"Cannot connect to {c['host']}",
                "metrics": {},
            }, {**state, "ssh": None}
        state = {**state, "ssh": ssh}

    os_type = c.get("os", "linux").lower()
    data    = _run(ssh, os_type)

    if data is None:
        # Force reconnect next poll
        return {
            "health":  "error",
            "message": "Poll script failed",
            "metrics": {},
        }, {**state, "ssh": None}

    metrics, state = _parse(data, state)
    health, message = _apply_health_rules(metrics, c.get("health_rules", []))
    return {"health": health, "message": message, "metrics": metrics}, state


# ── internals ────────────────────────────────────────────────────────────── #

def _connect(c: dict):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw = dict(
            hostname     = c["host"],
            port         = int(c.get("port", 22)),
            username     = c["user"],
            timeout      = 10,
            banner_timeout = 10,
            auth_timeout   = 10,
        )
        if c.get("key"):
            kw["key_filename"] = c["key"]
        elif c.get("password"):
            kw["password"] = c["password"]
        client.connect(**kw)
        log.info("SSH connected to %s", c["host"])
        return client
    except Exception as exc:
        log.warning("SSH connect failed (%s): %s", c.get("host", "?"), exc)
        return None


def _run(ssh, os_type: str):
    script = _WINDOWS_SCRIPT if os_type == "windows" else _LINUX_SCRIPT
    py     = "python" if os_type == "windows" else "python3"
    try:
        transport = ssh.get_transport()
        if transport is None or not transport.is_active():
            return None
        stdin, stdout, _ = ssh.exec_command(f"{py} -", timeout=15)
        stdin.write(script)
        stdin.close()
        stdout.channel.settimeout(15)
        raw = stdout.read().decode(errors="replace").strip()
        return json.loads(raw) if raw else None
    except Exception as exc:
        log.warning("SSH poll error: %s", exc)
        return None


def _apply_health_rules(metrics: dict, rules: list) -> tuple[str, str]:
    health  = "good"
    message = "Connected"
    for rule in rules:
        metric = rule.get("metric")
        if metric not in metrics:
            continue
        val = metrics[metric]
        if rule.get("error_if_zero") and val == 0:
            health  = "error"
            message = f"{metric} is zero"
        elif "error_above" in rule and val > rule["error_above"]:
            health  = "error"
            message = f"{metric} {val:.1f} > {rule['error_above']}"
        elif "warn_above" in rule and val > rule["warn_above"] and health == "good":
            health  = "warning"
            message = f"{metric} {val:.1f} > {rule['warn_above']}"
    return health, message


def _parse(data: dict, state: dict) -> tuple[dict, dict]:
    now      = time.monotonic()
    raw_sent = float(data.get("net_sent", 0))
    raw_recv = float(data.get("net_recv", 0))

    prev_sent = state.get("prev_sent")
    prev_recv = state.get("prev_recv")
    prev_t    = state.get("prev_t")

    if prev_sent is not None and prev_t is not None:
        dt = now - prev_t
        if dt > 0.01:
            net_in  = max(0.0, (raw_recv - prev_recv) / dt / 1_048_576)
            net_out = max(0.0, (raw_sent - prev_sent) / dt / 1_048_576)
        else:
            net_in = net_out = 0.0
    else:
        net_in = net_out = 0.0

    metrics = {
        "cpu":      float(data.get("cpu",      0)),
        "ram":      float(data.get("ram",      0)),
        "disk":     float(data.get("disk",     0)),
        "ctx_rate": float(data.get("ctx_rate", 0)),
        "load1":    float(data.get("load1",    0)),
        "net_in":   min(net_in,  100.0),
        "net_out":  min(net_out, 100.0),
    }
    for i, v in enumerate(data.get("cores", [])):
        metrics[f"core_{i}"] = float(v)

    new_state = {
        **state,
        "prev_sent": raw_sent,
        "prev_recv": raw_recv,
        "prev_t":    now,
    }
    return metrics, new_state
