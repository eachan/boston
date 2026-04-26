from __future__ import annotations

import json
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import paramiko
from scp import SCPClient


CONFIG_FILE = Path(__file__).resolve().parent / "manager_config.json"


DEFAULT_APPS = [
    {"name": "Boston Referee", "service": "boston-referee"},
    {"name": "Boston Dashboard", "service": "boston-dashboard"},
    {"name": "Local Model (Ollama)", "service": "ollama"},
]


class PiManagerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Boston Pi Deployment Manager")
        self.root.geometry("980x720")

        self.config = self._load_config()
        self.apps = self.config.get("apps", DEFAULT_APPS.copy())

        self.host_var = tk.StringVar(value=self.config.get("host", ""))
        self.port_var = tk.StringVar(value=str(self.config.get("port", 22)))
        self.user_var = tk.StringVar(value=self.config.get("username", "pi"))
        self.password_var = tk.StringVar(value=self.config.get("password", ""))
        self.remote_dir_var = tk.StringVar(value=self.config.get("remote_dir", "/home/pi/boston"))
        self.local_project_dir_var = tk.StringVar(value=self.config.get("local_project_dir", ""))

        self.client: paramiko.SSHClient | None = None

        self._build_ui()
        self._render_app_buttons()

    def _build_ui(self) -> None:
        header = ttk.Label(self.root, text="Boston Pi Deployment Manager", font=("Segoe UI", 16, "bold"))
        header.pack(anchor="w", padx=14, pady=(12, 8))

        conn = ttk.LabelFrame(self.root, text="Connection")
        conn.pack(fill="x", padx=14, pady=6)

        fields = [
            ("Host", self.host_var),
            ("Port", self.port_var),
            ("Username", self.user_var),
            ("Password", self.password_var),
            ("Remote Dir", self.remote_dir_var),
        ]
        for i, (label, var) in enumerate(fields):
            ttk.Label(conn, text=label).grid(row=0, column=i * 2, sticky="w", padx=5, pady=8)
            show = "*" if label == "Password" else None
            ttk.Entry(conn, textvariable=var, width=16 if label != "Remote Dir" else 28, show=show).grid(
                row=0, column=i * 2 + 1, sticky="we", padx=5, pady=8
            )

        ttk.Button(conn, text="Save", command=self.save_config).grid(row=1, column=0, padx=5, pady=8)
        ttk.Button(conn, text="Connect SSH", command=self.connect).grid(row=1, column=1, padx=5, pady=8)
        ttk.Button(conn, text="Disconnect", command=self.disconnect).grid(row=1, column=2, padx=5, pady=8)

        actions = ttk.LabelFrame(self.root, text="Deployment")
        actions.pack(fill="x", padx=14, pady=6)
        path_row = ttk.Frame(actions)
        path_row.pack(fill="x", padx=6, pady=(8, 2))
        ttk.Label(path_row, text="Local Project Dir").pack(side="left")
        ttk.Entry(path_row, textvariable=self.local_project_dir_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(path_row, text="Browse", command=self.choose_local_project_dir).pack(side="left")

        action_row = ttk.Frame(actions)
        action_row.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(action_row, text="Upload File (SCP)", command=self.upload_file).pack(side="left", padx=6)
        ttk.Button(action_row, text="Download File (SCP)", command=self.download_file).pack(side="left", padx=6)
        ttk.Button(action_row, text="One-click Deploy", command=self.deploy).pack(side="left", padx=6)

        self.apps_frame = ttk.LabelFrame(self.root, text="Managed Apps / Services")
        self.apps_frame.pack(fill="x", padx=14, pady=6)
        ttk.Button(self.apps_frame, text="+ Add App/Service", command=self.add_app).pack(
            anchor="w", padx=6, pady=(6, 2)
        )

        sys_frame = ttk.LabelFrame(self.root, text="System")
        sys_frame.pack(fill="x", padx=14, pady=6)
        ttk.Button(sys_frame, text="Reboot Pi", command=lambda: self.run_remote("sudo reboot")).pack(
            side="left", padx=6, pady=8
        )
        ttk.Button(sys_frame, text="Shutdown Pi", command=lambda: self.run_remote("sudo shutdown now")).pack(
            side="left", padx=6, pady=8
        )

        logs = ttk.LabelFrame(self.root, text="Output")
        logs.pack(fill="both", expand=True, padx=14, pady=6)
        self.log = tk.Text(logs, height=20)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def _render_app_buttons(self) -> None:
        for child in list(self.apps_frame.winfo_children()):
            if isinstance(child, ttk.Frame):
                child.destroy()

        for app in self.apps:
            row = ttk.Frame(self.apps_frame)
            row.pack(fill="x", padx=6, pady=3)
            ttk.Label(row, text=f"{app['name']} ({app['service']})", width=38).pack(side="left")
            ttk.Button(row, text="Start", command=lambda s=app["service"]: self.service_action(s, "start")).pack(
                side="left", padx=4
            )
            ttk.Button(row, text="Stop", command=lambda s=app["service"]: self.service_action(s, "stop")).pack(
                side="left", padx=4
            )
            ttk.Button(
                row,
                text="Restart",
                command=lambda s=app["service"]: self.service_action(s, "restart"),
            ).pack(side="left", padx=4)

    def save_config(self) -> None:
        self.config.update(
            {
                "host": self.host_var.get().strip(),
                "port": int(self.port_var.get().strip() or "22"),
                "username": self.user_var.get().strip(),
                "password": self.password_var.get(),
                "remote_dir": self.remote_dir_var.get().strip(),
                "local_project_dir": self.local_project_dir_var.get().strip(),
                "apps": self.apps,
            }
        )
        CONFIG_FILE.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        self._append_log("Configuration saved.")

    def _load_config(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {
            "apps": DEFAULT_APPS.copy(),
            "port": 22,
            "username": "pi",
            "remote_dir": "/home/pi/boston",
            "local_project_dir": "",
        }

    def choose_local_project_dir(self) -> None:
        initial = self.local_project_dir_var.get().strip() or str(Path.cwd())
        selected = filedialog.askdirectory(title="Select local project folder", initialdir=initial)
        if not selected:
            return
        self.local_project_dir_var.set(selected)
        self.save_config()
        self._append_log(f"Local project directory set: {selected}")

    def connect(self) -> None:
        self.save_config()

        def _connect() -> None:
            try:
                self._append_log("Connecting...")
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=self.host_var.get().strip(),
                    port=int(self.port_var.get().strip()),
                    username=self.user_var.get().strip(),
                    password=self.password_var.get(),
                    timeout=8,
                )
                self.client = client
                self._append_log("SSH connected.")
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"SSH connection failed: {exc}")

        threading.Thread(target=_connect, daemon=True).start()

    def disconnect(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
            self._append_log("SSH disconnected.")

    def upload_file(self) -> None:
        if not self._require_connection():
            return
        local = filedialog.askopenfilename(title="Select local file")
        if not local:
            return
        remote = simpledialog.askstring("Remote path", "Enter remote path:", initialvalue=self.remote_dir_var.get())
        if not remote:
            return

        def _upload() -> None:
            assert self.client is not None
            with SCPClient(self.client.get_transport()) as scp:
                scp.put(local, remote)
            self._append_log(f"Uploaded {local} -> {remote}")

        threading.Thread(target=_upload, daemon=True).start()

    def download_file(self) -> None:
        if not self._require_connection():
            return
        remote = simpledialog.askstring("Remote path", "Enter remote file path:")
        if not remote:
            return
        local = filedialog.asksaveasfilename(title="Save as")
        if not local:
            return

        def _download() -> None:
            assert self.client is not None
            with SCPClient(self.client.get_transport()) as scp:
                scp.get(remote, local)
            self._append_log(f"Downloaded {remote} -> {local}")

        threading.Thread(target=_download, daemon=True).start()

    def deploy(self) -> None:
        if not self._require_connection():
            return
        self.save_config()

        local_root = self.local_project_dir_var.get().strip()
        local_root_path = Path(local_root) if local_root else None
        if local_root_path is None or not local_root_path.exists() or not local_root_path.is_dir():
            self._append_log("Local project directory is not set or invalid. Please select it.")
            selected = filedialog.askdirectory(title="Select local project folder")
            if not selected:
                return
            self.local_project_dir_var.set(selected)
            self.save_config()
            local_root_path = Path(selected)

        remote_root = self.remote_dir_var.get().strip()
        remote_user = self.user_var.get().strip()

        commands = [
            f"mkdir -p {remote_root}",
            f"cd {remote_root} && python3 -m venv .venv || true",
            f"cd {remote_root} && . .venv/bin/activate && pip install --upgrade pip",
            f"cd {remote_root} && . .venv/bin/activate && pip install -r requirements.txt",
            f"cd {remote_root} && . .venv/bin/activate && pip install --upgrade piper-tts || true",
            (
                f"cd {remote_root} && "
                f"sed \"s|User=pi|User={remote_user}|; s|/home/pi/boston|{remote_root}|g\" "
                f"systemd/boston-referee.service > /tmp/boston-referee.service"
            ),
            (
                f"cd {remote_root} && "
                f"sed \"s|User=pi|User={remote_user}|; s|/home/pi/boston|{remote_root}|g\" "
                f"systemd/boston-dashboard.service > /tmp/boston-dashboard.service"
            ),
            "sudo apt-get update -y",
            "sudo apt-get install -y nginx espeak espeak-ng libespeak1 libespeak-ng1 wget unzip curl mpg123 vlc alsa-utils",
            "if ! command -v ollama >/dev/null 2>&1; then curl -fsSL https://ollama.com/install.sh | sh; fi || true",
            "sudo systemctl enable ollama || true",
            "sudo systemctl restart ollama || true",
            "ollama pull llama3.2:3b || true",
            f"mkdir -p {remote_root}/models",
            (
                f"if [ ! -d {remote_root}/models/vosk ] || "
                f"[ -z \"$(ls -A {remote_root}/models/vosk 2>/dev/null)\" ]; then "
                f"cd {remote_root}/models && "
                f"(wget -O vosk.zip https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip "
                f"|| curl -L -o vosk.zip https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip) && "
                f"unzip -o vosk.zip && rm -f vosk.zip && rm -rf vosk && "
                f"model_dir=$(find . -maxdepth 1 -type d -name 'vosk-model*' | head -n 1) && "
                f"[ -n \"$model_dir\" ] && mv \"$model_dir\" vosk; "
                f"else echo 'Vosk model already present, skipping download.'; fi"
            ),
            f"test -f {remote_root}/models/vosk/am/final.mdl",
            f"mkdir -p {remote_root}/models/piper",
            (
                f"if [ ! -f {remote_root}/models/piper/en_US-lessac-medium.onnx ]; then "
                f"(wget -O {remote_root}/models/piper/en_US-lessac-medium.onnx "
                f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx "
                f"|| curl -L -o {remote_root}/models/piper/en_US-lessac-medium.onnx "
                f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx); "
                f"fi"
            ),
            (
                f"if [ ! -f {remote_root}/models/piper/en_US-lessac-medium.onnx.json ]; then "
                f"(wget -O {remote_root}/models/piper/en_US-lessac-medium.onnx.json "
                f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json "
                f"|| curl -L -o {remote_root}/models/piper/en_US-lessac-medium.onnx.json "
                f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json); "
                f"fi"
            ),
            "sudo cp /tmp/boston-referee.service /etc/systemd/system/boston-referee.service",
            "sudo cp /tmp/boston-dashboard.service /etc/systemd/system/boston-dashboard.service",
            f"cd {remote_root} && sudo cp systemd/boston-volume.service /etc/systemd/system/boston-volume.service",
            (
                "dashboard_user=$(awk -F= '/^User=/{print $2}' /tmp/boston-dashboard.service | head -n 1 | tr -d '\\r' | xargs) && "
                "if [ -z \"$dashboard_user\" ]; then dashboard_user=pi; fi && "
                "printf '%s ALL=(root) NOPASSWD: /sbin/reboot, /sbin/shutdown, /bin/systemctl\\n' \"$dashboard_user\" "
                "| sudo tee /etc/sudoers.d/boston-dashboard >/dev/null && "
                "sudo chmod 440 /etc/sudoers.d/boston-dashboard && "
                "sudo visudo -cf /etc/sudoers.d/boston-dashboard"
            ),
            f"cd {remote_root} && sudo cp systemd/boston-nginx.conf /etc/nginx/sites-available/boston",
            "sudo ln -sf /etc/nginx/sites-available/boston /etc/nginx/sites-enabled/boston",
            "sudo rm -f /etc/nginx/sites-enabled/default",
            "sudo nginx -t",
            "sudo systemctl restart nginx",
            "sudo systemctl enable nginx",
            "sudo systemctl daemon-reload",
            "sudo systemctl enable boston-volume",
            "sudo systemctl start boston-volume",
            "sudo systemctl enable boston-referee",
            "sudo systemctl enable boston-dashboard",
            "sudo systemctl restart boston-referee",
            "sudo systemctl restart boston-dashboard",
        ]

        def _deploy() -> None:
            assert self.client is not None
            sudo_probe = self._run_and_log("sudo -k true")
            if sudo_probe != 0:
                self._append_log(
                    "Sudo authentication failed. Confirm this account has sudo rights and password is correct."
                )
                return
            self._run_and_log(f"mkdir -p {remote_root}")
            self._append_log("Uploading project files...")
            with SCPClient(self.client.get_transport()) as scp:
                for child in local_root_path.iterdir():
                    scp.put(str(child), remote_path=remote_root, recursive=child.is_dir())
            self._append_log("Upload complete. Running remote setup commands...")
            for cmd in commands:
                exit_code = self._run_and_log(cmd)
                if exit_code != 0:
                    self._append_log(f"Deploy halted due to command failure (exit {exit_code}).")
                    return
            self._append_log("Deploy complete.")

        threading.Thread(target=_deploy, daemon=True).start()

    def add_app(self) -> None:
        name = simpledialog.askstring("App Name", "Display name:")
        if not name:
            return
        service = simpledialog.askstring("Service Name", "systemd service name:")
        if not service:
            return
        self.apps.append({"name": name.strip(), "service": service.strip()})
        self.save_config()
        self._render_app_buttons()

    def service_action(self, service: str, action: str) -> None:
        self.run_remote(f"sudo systemctl {action} {service}")

    def run_remote(self, command: str) -> None:
        if not self._require_connection():
            return

        def _work() -> None:
            self._run_and_log(command)

        threading.Thread(target=_work, daemon=True).start()

    def _run_and_log(self, command: str) -> int:
        assert self.client is not None
        self._append_log(f"$ {command}")
        prepared_command, needs_sudo = self._prepare_command(command)
        stdin, stdout, stderr = self.client.exec_command(prepared_command, get_pty=needs_sudo)
        if needs_sudo:
            password = self.password_var.get()
            if not password:
                self._append_log("Warning: sudo command detected but password is empty.")
            stdin.write(password + "\n")
            stdin.flush()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        exit_status = stdout.channel.recv_exit_status()
        if out:
            self._append_log(out)
        if err:
            self._append_log(err)
        return int(exit_status)

    @staticmethod
    def _prepare_command(command: str) -> tuple[str, bool]:
        prepared = re.sub(r"(?<!\S)sudo\s+(?!-S)", "sudo -S -p '' ", command)
        needs_sudo = "sudo -S -p '' " in prepared
        return prepared, needs_sudo

    def _append_log(self, text: str) -> None:
        self.log.insert("end", f"{text}\n")
        self.log.see("end")

    def _require_connection(self) -> bool:
        if self.client is None:
            messagebox.showwarning("Not connected", "Please connect SSH first.")
            return False
        return True


def main() -> None:
    root = tk.Tk()
    app = PiManagerApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.disconnect(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
