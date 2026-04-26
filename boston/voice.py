from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path

import numpy as np


LOGGER = logging.getLogger(__name__)

try:
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model
except Exception:  # noqa: BLE001
    sd = None
    KaldiRecognizer = None
    Model = None


class VoiceListener:
    """Microphone listener with offline Vosk speech recognition."""

    def __init__(
        self,
        model_path: str,
        sample_rate: int = 16_000,
        channels: int = 1,
        blocksize: int = 8_000,
        input_gain: float = 1.0,
        use_command_grammar: bool = False,
        command_phrases: list[str] | None = None,
        partial_min_words: int = 3,
    ) -> None:
        self.model_path = Path(model_path)
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.input_gain = max(0.2, min(6.0, input_gain))
        self.use_command_grammar = use_command_grammar
        self.command_phrases = [p.strip() for p in (command_phrases or []) if p.strip()]
        self.partial_min_words = max(1, partial_min_words)

        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self._transcript_queue: queue.Queue[str] = queue.Queue()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stream = None
        self._recognizer = None
        self._available = False
        self._unavailable_reason = "not_initialized"

        self._load_model()

    def _load_model(self) -> None:
        if Model is None or KaldiRecognizer is None or sd is None:
            LOGGER.warning("Voice dependencies not available; voice listener disabled.")
            self._available = False
            self._unavailable_reason = "missing_python_dependencies"
            return
        if not self.model_path.exists():
            LOGGER.warning("Vosk model path missing: %s. Voice listener disabled.", self.model_path)
            self._available = False
            self._unavailable_reason = f"missing_vosk_model:{self.model_path}"
            return

        model = Model(str(self.model_path))
        self._recognizer = KaldiRecognizer(model, self.sample_rate)
        if self.use_command_grammar and self.command_phrases:
            grammar_items = list(dict.fromkeys(self.command_phrases + ["[unk]"]))
            grammar = json.dumps(grammar_items)
            self._recognizer = KaldiRecognizer(model, self.sample_rate, grammar)

        self._available = True
        self._unavailable_reason = ""

    @property
    def available(self) -> bool:
        return self._available

    @property
    def running(self) -> bool:
        return self._running

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def start(self) -> None:
        if self._running or not self._available:
            if not self._available:
                LOGGER.warning("Voice listener start skipped: %s", self._unavailable_reason)
            return

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                LOGGER.warning("Audio callback status: %s", status)
            processed = self._apply_gain(bytes(indata))
            self._audio_queue.put(processed)

        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            dtype="int16",
            channels=self.channels,
            callback=callback,
        )
        self._stream.start()
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True, name="boston-voice")
        self._thread.start()
        LOGGER.info("Voice listener started")

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed closing audio stream")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _worker(self) -> None:
        while self._running and self._recognizer is not None:
            try:
                data = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if self._recognizer.AcceptWaveform(data):
                result = self._recognizer.Result()
                text = self._extract_text(result)
                if text:
                    LOGGER.info("Voice transcript (final): %s", text)
                    self._transcript_queue.put(text)
            else:
                partial = self._extract_text(self._recognizer.PartialResult(), key="partial")
                if partial and len(partial.split()) >= self.partial_min_words:
                    LOGGER.debug("Voice transcript (partial): %s", partial)

    def _apply_gain(self, raw_audio: bytes) -> bytes:
        if self.input_gain == 1.0:
            return raw_audio
        pcm = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32)
        pcm *= self.input_gain
        pcm = np.clip(pcm, -32768, 32767).astype(np.int16)
        return pcm.tobytes()

    @staticmethod
    def _extract_text(payload: str, key: str = "text") -> str:
        try:
            parsed = json.loads(payload)
            return str(parsed.get(key, "")).strip()
        except json.JSONDecodeError:
            return ""

    def get_next_transcript(self, timeout: float = 0.0) -> str | None:
        try:
            return self._transcript_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def inject_transcript(self, text: str) -> None:
        """Testing and fallback path when no microphone is available."""
        if text.strip():
            self._transcript_queue.put(text.strip())
