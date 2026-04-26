from __future__ import annotations

import platform
import socket
from pathlib import Path
from typing import Any

import psutil

from boston.utils import run_command


def get_system_stats() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "hostname": platform.node(),
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "memory_percent": vm.percent,
        "memory_used_mb": round(vm.used / (1024 * 1024), 1),
        "memory_total_mb": round(vm.total / (1024 * 1024), 1),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024 * 1024 * 1024), 2),
        "disk_total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
        "temperature_c": get_temperature_c(),
    }


def get_temperature_c() -> float | None:
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if thermal_path.exists():
        try:
            raw = thermal_path.read_text(encoding="utf-8").strip()
            return round(int(raw) / 1000.0, 1)
        except Exception:  # noqa: BLE001
            return None
    vcgencmd = run_command("vcgencmd measure_temp")
    if vcgencmd.ok and "temp=" in vcgencmd.stdout:
        try:
            text = vcgencmd.stdout.split("temp=")[-1].replace("'C", "")
            return float(text)
        except Exception:  # noqa: BLE001
            return None
    return None


def get_hailo_status(command: str) -> dict[str, Any]:
    result = run_command(command)
    return {
        "ok": result.ok,
        "summary": "available" if result.ok else "unavailable",
        "detail": result.stdout if result.ok else (result.stderr or result.stdout),
    }


def get_audio_input_status() -> dict[str, Any]:
    result = run_command("arecord -l")
    return {
        "ok": result.ok,
        "summary": "available" if result.ok else "unavailable",
        "detail": result.stdout if result.ok else (result.stderr or "no input devices"),
    }


def get_audio_output_status() -> dict[str, Any]:
    result = run_command("aplay -l")
    return {
        "ok": result.ok,
        "summary": "available" if result.ok else "unavailable",
        "detail": result.stdout if result.ok else (result.stderr or "no output devices"),
    }


def get_local_ip_address() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:  # noqa: BLE001
        pass
    finally:
        sock.close()

    host_result = run_command("hostname -I")
    if host_result.ok and host_result.stdout:
        candidates = [p.strip() for p in host_result.stdout.split() if p.strip() and not p.startswith("127.")]
        if candidates:
            return candidates[0]

    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidate = sockaddr[0]
            if candidate and not candidate.startswith("127."):
                return candidate
    except Exception:  # noqa: BLE001
        return None
    return None
