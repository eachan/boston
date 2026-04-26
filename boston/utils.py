from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(command: str, timeout: int = 20) -> CommandResult:
    completed = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    result = CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=(completed.stdout or "").strip(),
        stderr=(completed.stderr or "").strip(),
    )
    if not result.ok:
        LOGGER.warning("Command failed (%s): %s", result.returncode, command)
    return result


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return default or {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default or {}
