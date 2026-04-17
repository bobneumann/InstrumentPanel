"""
SNMP v2c collector using the system snmpget binary (net-snmp).

No Python SNMP library required — uses subprocess to call snmpget,
which is OS-maintained and widely audited.

Installation:
  Windows: choco install net-snmp  (or download from net-snmp.org)
  Linux:   sudo apt install snmp
  macOS:   brew install net-snmp

config.collector fields:
  host          Hostname or IP
  port          UDP port (default 161)
  community     Community string (default "public")
  snmpget_path  Full path to snmpget binary (default: "snmpget", assumes it's on PATH)
  oids          {metric_name: oid_string, ...}
  health_rules  list of rule dicts (optional):
    {"metric": "cpu_pct", "warn_above": 80, "error_above": 95}
    {"metric": "uptime",  "error_if_zero": true}
"""

import subprocess
import logging

log = logging.getLogger(__name__)


def poll(config: dict, state: dict) -> tuple[dict, dict]:
    c         = config["collector"]
    host      = c["host"]
    port      = int(c.get("port", 161))
    community = c.get("community", "public")
    oids      = c.get("oids", {})
    snmpget   = c.get("snmpget_path", "snmpget")

    if not oids:
        return {"health": "unknown", "message": "No OIDs configured", "metrics": {}}, state

    # Single subprocess call fetches all OIDs at once
    cmd = [
        snmpget,
        "-v", "2c",
        "-c", community,
        "-OqnT",          # quiet, numeric OIDs, timeticks as integers
        f"{host}:{port}",
        *oids.values(),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output = True,
            text           = True,
            timeout        = 10,
        )
    except FileNotFoundError:
        return {
            "health":  "error",
            "message": f"snmpget not found — install net-snmp ({snmpget})",
            "metrics": {},
        }, state
    except subprocess.TimeoutExpired:
        return {"health": "error", "message": f"SNMP timeout ({host})", "metrics": {}}, state
    except Exception as exc:
        return {"health": "error", "message": f"SNMP error: {exc}", "metrics": {}}, state

    # Parse output: one line per OID, format ".oid value"
    metrics:  dict[str, float] = {}
    failed:   list[str]        = []
    oid_to_name = {v: k for k, v in oids.items()}

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        raw_oid, raw_val = parts
        # snmpget prefixes numeric OIDs with "." — normalize both sides
        oid = raw_oid.lstrip(".")
        name = oid_to_name.get(oid) or oid_to_name.get(f".{oid}")
        if name is None:
            continue
        try:
            metrics[name] = float(raw_val.strip())
        except ValueError:
            log.warning("SNMP %s/%s: cannot parse value %r", host, name, raw_val)
            failed.append(name)

    # Any OID that didn't appear in output failed
    for name in oids:
        if name not in metrics and name not in failed:
            failed.append(name)

    if result.returncode != 0 and not metrics:
        err = (result.stderr or result.stdout).strip()[:200]
        return {"health": "error", "message": f"snmpget failed: {err}", "metrics": {}}, state

    # Apply health rules
    health  = "good"
    message = "OK"

    for rule in c.get("health_rules", []):
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

    if failed:
        health  = "error"
        message = f"SNMP failed: {', '.join(failed)}"

    return {"health": health, "message": message, "metrics": metrics}, state
