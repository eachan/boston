from __future__ import annotations

import logging
from dataclasses import dataclass

from boston.utils import CommandResult, run_command


LOGGER = logging.getLogger(__name__)
SUDO_HINT = (
    "Dashboard action requires passwordless sudo for this command. "
    "Run the Boston installer/deploy flow to install /etc/sudoers.d/boston-dashboard."
)


@dataclass
class ServiceActionResult:
    ok: bool
    message: str
    command: str


class SystemController:
    def __init__(self) -> None:
        pass

    def service_status(self, service: str) -> str:
        result = run_command(f"systemctl is-active {service}")
        return result.stdout or "unknown"

    def service_start(self, service: str) -> ServiceActionResult:
        return self._run_service_command(service, "start")

    def service_stop(self, service: str) -> ServiceActionResult:
        return self._run_service_command(service, "stop")

    def service_restart(self, service: str) -> ServiceActionResult:
        return self._run_service_command(service, "restart")

    def reboot(self) -> ServiceActionResult:
        cmd = "sudo -n /sbin/reboot"
        result = run_command(cmd)
        return ServiceActionResult(
            ok=result.ok,
            message="Reboot command sent" if result.ok else self._format_privileged_error(result),
            command=cmd,
        )

    def shutdown(self) -> ServiceActionResult:
        cmd = "sudo -n /sbin/shutdown -h now"
        result = run_command(cmd)
        return ServiceActionResult(
            ok=result.ok,
            message="Shutdown command sent" if result.ok else self._format_privileged_error(result),
            command=cmd,
        )

    def add_wifi_network(self, ssid: str, password: str) -> ServiceActionResult:
        safe_ssid = ssid.replace('"', "")
        safe_password = password.replace('"', "")
        cmd = f'nmcli dev wifi connect "{safe_ssid}" password "{safe_password}"'
        result = run_command(cmd, timeout=30)
        if result.ok:
            return ServiceActionResult(ok=True, message="Wi-Fi network added successfully", command=cmd)

        fallback_cmd = (
            "sudo bash -c \"wpa_passphrase '{ssid}' '{pw}' >> /etc/wpa_supplicant/wpa_supplicant.conf && "
            "wpa_cli -i wlan0 reconfigure\""
        ).format(ssid=safe_ssid.replace("'", ""), pw=safe_password.replace("'", ""))
        fallback = run_command(fallback_cmd, timeout=30)
        ok = fallback.ok
        message = (
            "Wi-Fi network added using wpa_supplicant fallback"
            if ok
            else f"nmcli failed: {result.stderr or result.stdout}; fallback failed: {fallback.stderr or fallback.stdout}"
        )
        return ServiceActionResult(ok=ok, message=message, command=fallback_cmd)

    def _run_service_command(self, service: str, action: str) -> ServiceActionResult:
        command = f"sudo -n /bin/systemctl {action} {service}"
        result: CommandResult = run_command(command)
        if result.ok:
            msg = f"{service} {action} successful"
            LOGGER.info(msg)
            return ServiceActionResult(ok=True, message=msg, command=command)
        msg = self._format_privileged_error(result)
        LOGGER.warning(msg)
        return ServiceActionResult(ok=False, message=msg, command=command)

    @staticmethod
    def _format_privileged_error(result: CommandResult) -> str:
        detail = (result.stderr or result.stdout or "command failed").strip()
        lower = detail.lower()
        if "password is required" in lower or "terminal is required" in lower or "a terminal is required" in lower:
            return f"{detail}. {SUDO_HINT}"
        return detail
