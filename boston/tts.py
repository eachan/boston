from __future__ import annotations

import logging
import queue
import re
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import pyttsx3


LOGGER = logging.getLogger(__name__)


@dataclass
class SpeechMessage:
    text: str
    priority: int = 0


class SpeechSynthesizer:
    """Threaded TTS wrapper so speech calls never block the referee pipeline."""

    def __init__(
        self,
        enabled: bool = True,
        backend: str = "auto",
        rate: int = 155,
        volume: float = 1.0,
        preferred_voice_tokens: list[str] | None = None,
        fallback_voice_id: str | None = "english-us+f3",
        piper_command: str = ".venv/bin/piper",
        piper_model_path: str = "models/piper/en_US-lessac-medium.onnx",
        piper_model_config_path: str = "models/piper/en_US-lessac-medium.onnx.json",
    ) -> None:
        self._queue: queue.PriorityQueue[tuple[int, int, SpeechMessage]] = queue.PriorityQueue()
        self._counter = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._engine = None
        self._available = bool(enabled)
        self._unavailable_reason = ""
        self._backend = (backend or "auto").lower().strip()

        self._rate = rate
        self._volume = max(0.0, min(1.0, volume))
        self._preferred_voice_tokens = [t.lower() for t in (preferred_voice_tokens or [])]
        self._fallback_voice_id = fallback_voice_id
        self._piper_command = piper_command
        self._piper_model_path = piper_model_path
        self._piper_model_config_path = piper_model_config_path
        self._espeak_command = self._detect_espeak_command()

        if not self._available:
            self._unavailable_reason = "tts_disabled_by_config"
            LOGGER.info("TTS disabled by configuration")
            return

        self._available = self._initialize_backend()

    def _initialize_backend(self) -> bool:
        if self._backend in {"piper", "auto"} and self._is_piper_ready():
            self._backend = "piper"
            LOGGER.info("TTS backend selected: piper")
            return True

        if self._backend == "piper":
            self._unavailable_reason = "piper_not_ready"
            LOGGER.warning("Piper requested but missing command/model files")
            # Fall through to other backends if available.

        if self._init_pyttsx3_engine():
            self._backend = "pyttsx3"
            LOGGER.info("TTS backend selected: pyttsx3")
            return True

        if self._espeak_command:
            self._backend = "espeak_cli"
            LOGGER.warning("TTS backend selected: %s fallback", self._espeak_command)
            return True

        self._unavailable_reason = self._unavailable_reason or "no_tts_backend_available"
        return False

    def _init_pyttsx3_engine(self) -> bool:
        try:
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self._rate)
            self._engine.setProperty("volume", self._volume)
            self._select_voice()
            return True
        except Exception as exc:  # noqa: BLE001
            self._engine = None
            self._unavailable_reason = str(exc)
            LOGGER.exception("TTS engine unavailable: %s", exc)
            return False

    @staticmethod
    def _detect_espeak_command() -> str | None:
        for cmd in ("espeak-ng", "espeak"):
            if shutil.which(cmd):
                return cmd
        return None

    def _resolve_path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (Path.cwd() / p)

    def _is_piper_ready(self) -> bool:
        command_path = self._resolve_path(self._piper_command)
        model_path = self._resolve_path(self._piper_model_path)
        config_path = self._resolve_path(self._piper_model_config_path)
        return command_path.exists() and model_path.exists() and config_path.exists()

    @property
    def backend(self) -> str:
        return self._backend

    def _select_voice(self) -> None:
        if self._engine is None:
            return
        try:
            voices = self._engine.getProperty("voices") or []
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed reading available voices")
            return

        def _voice_text(voice) -> str:  # noqa: ANN001
            parts = [str(getattr(voice, "id", "")), str(getattr(voice, "name", ""))]
            languages = getattr(voice, "languages", []) or []
            parts.extend(str(lang) for lang in languages)
            return " ".join(parts).lower()

        selected_id = None
        if self._preferred_voice_tokens:
            best_score = 0
            for voice in voices:
                text = _voice_text(voice)
                score = sum(1 for token in self._preferred_voice_tokens if token in text)
                if score > best_score:
                    best_score = score
                    selected_id = getattr(voice, "id", None)
            if best_score == 0:
                selected_id = None

        if not selected_id and self._fallback_voice_id:
            selected_id = self._fallback_voice_id

        if selected_id:
            try:
                self._engine.setProperty("voice", selected_id)
                LOGGER.info("TTS selected voice: %s", selected_id)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed setting preferred voice: %s", selected_id)

    def start(self) -> None:
        if self._running:
            return
        if not self._available:
            LOGGER.warning("TTS disabled at startup: %s", self._unavailable_reason or "unknown")
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True, name="boston-tts")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self.say("", priority=1000)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def say(self, text: str, priority: int = 0) -> None:
        normalized_text = self._normalize_pronunciation(text)
        if not normalized_text.strip():
            return
        if not self._available:
            LOGGER.info("TTS disabled, text output only => %s", normalized_text)
            return
        with self._lock:
            self._counter += 1
            seq = self._counter
        self._queue.put((priority, seq, SpeechMessage(text=normalized_text, priority=priority)))

    @staticmethod
    def _normalize_pronunciation(text: str) -> str:
        normalized = text
        normalized = re.sub(r"\bkumite\b", "koo-mee-teh", normalized, flags=re.IGNORECASE)
        return normalized

    def _worker(self) -> None:
        while self._running:
            try:
                _, _, msg = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not msg.text.strip():
                continue
            LOGGER.info("TTS => %s", msg.text)
            try:
                self._speak_with_active_backend(msg.text)
            except Exception:  # noqa: BLE001
                LOGGER.exception("TTS failure, message: %s", msg.text)
                if self._attempt_runtime_fallback(msg.text):
                    continue

    def _speak_with_active_backend(self, text: str) -> None:
        if self._backend == "piper":
            self._speak_with_piper(text)
            return
        if self._backend == "pyttsx3":
            if self._engine is None:
                raise RuntimeError("pyttsx3 engine not initialized")
            self._engine.say(text)
            self._engine.runAndWait()
            return
        if self._backend == "espeak_cli":
            self._speak_with_espeak(text)
            return
        raise RuntimeError(f"Unknown TTS backend: {self._backend}")

    def _attempt_runtime_fallback(self, text: str) -> bool:
        if self._backend == "piper" and self._init_pyttsx3_engine():
            self._backend = "pyttsx3"
            LOGGER.warning("Switched TTS backend from piper to pyttsx3 after runtime failure")
            try:
                self._speak_with_active_backend(text)
                return True
            except Exception:  # noqa: BLE001
                LOGGER.exception("pyttsx3 fallback also failed")

        if self._espeak_command:
            self._backend = "espeak_cli"
            LOGGER.warning("Switched TTS backend to %s fallback", self._espeak_command)
            try:
                self._speak_with_active_backend(text)
                return True
            except Exception:  # noqa: BLE001
                LOGGER.exception("espeak fallback failed")

        return False

    def _speak_with_piper(self, text: str) -> None:
        command_path = self._resolve_path(self._piper_command)
        model_path = self._resolve_path(self._piper_model_path)
        config_path = self._resolve_path(self._piper_model_config_path)

        cmd = (
            f"echo {shlex.quote(text)} | "
            f"{shlex.quote(str(command_path))} "
            f"--model {shlex.quote(str(model_path))} "
            f"--config {shlex.quote(str(config_path))} "
            "--output-raw | aplay -q -r 22050 -f S16_LE -t raw"
        )
        subprocess.run(cmd, shell=True, check=True, text=True)

    def _speak_with_espeak(self, text: str) -> None:
        if not self._espeak_command:
            raise RuntimeError("No espeak command available")
        cmd = f"{self._espeak_command} {shlex.quote(text)}"
        subprocess.run(cmd, shell=True, check=True, text=True)
