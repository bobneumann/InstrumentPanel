"""
TCP port connectivity check.

config.collector fields:
  host          Hostname or IP
  port          TCP port to probe
  timeout       Seconds before giving up (default 5)
  health_rules  list of rule dicts (optional):
    {"metric": "latency_ms", "warn_above": 20, "error_above": 100}
"""

import socket
import time


def poll(config: dict, state: dict) -> tuple[dict, dict]:
    c       = config["collector"]
    host    = c["host"]
    port    = int(c["port"])
    timeout = float(c.get("timeout", 5))

    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        latency_ms = (time.monotonic() - t0) * 1000
        metrics = {"up": 1.0, "latency_ms": latency_ms}
        health, message = _apply_health_rules(
            metrics, c.get("health_rules", []),
            default_message=f"Port {port} open ({latency_ms:.0f} ms)",
        )
        return {"health": health, "message": message, "metrics": metrics}, state
    except Exception as exc:
        return {
            "health":  "error",
            "message": f"Port {port} unreachable: {exc}",
            "metrics": {"up": 0.0, "latency_ms": 0.0},
        }, state


def _apply_health_rules(metrics: dict, rules: list, default_message: str = "OK") -> tuple[str, str]:
    health  = "good"
    message = default_message
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
