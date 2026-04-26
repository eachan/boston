from __future__ import annotations

import logging
import shlex
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from boston.config import AppConfig
from boston.hardware import (
    get_audio_input_status,
    get_audio_output_status,
    get_hailo_status,
    get_local_ip_address,
    get_system_stats,
)
from boston.intents import IntentParser
from boston.llm import LocalLLMClient
from boston.models import CommandIntent, IntentType, MatchContext, MatchState
from boston.rules import KumiteRuleEngine
from boston.storage import Storage
from boston.system_control import SystemController
from boston.tts import SpeechSynthesizer
from boston.utils import run_command
from boston.vision import VisionAnalyzer, select_best_hailo_model
from boston.voice import VoiceListener


LOGGER = logging.getLogger(__name__)


def _human_duration(seconds: int | None) -> str:
    if seconds is None:
        return "untimed"
    if seconds % 60 == 0:
        mins = seconds // 60
        return f"{mins} minute{'s' if mins != 1 else ''}"
    return f"{seconds} seconds"


class BostonReferee:
    def __init__(self, config: AppConfig, storage: Storage) -> None:
        self.config = config
        self.storage = storage
        self.system = SystemController()

        self.intent_parser = IntentParser(wake_word=config.audio.wake_word)
        self.tts = SpeechSynthesizer(
            enabled=config.tts.enabled,
            backend=config.tts.backend,
            rate=config.tts.rate,
            volume=config.tts.volume,
            preferred_voice_tokens=config.tts.preferred_voice_tokens,
            fallback_voice_id=config.tts.fallback_voice_id,
            piper_command=config.tts.piper_command,
            piper_model_path=config.tts.piper_model_path,
            piper_model_config_path=config.tts.piper_model_config_path,
        )
        self.voice = VoiceListener(
            model_path=config.audio.vosk_model_path,
            sample_rate=config.audio.sample_rate,
            channels=config.audio.channels,
            blocksize=config.audio.blocksize,
            input_gain=config.audio.input_gain,
            use_command_grammar=config.audio.use_command_grammar,
            command_phrases=config.audio.command_phrases,
            partial_min_words=config.audio.partial_min_words,
        )
        self.vision = VisionAnalyzer(
            camera_index=config.vision.camera_index,
            frame_width=config.vision.frame_width,
            frame_height=config.vision.frame_height,
            fps=config.vision.fps,
            detection_interval_seconds=config.vision.detection_interval_seconds,
            min_color_area=config.vision.min_color_area,
        )
        self.rules = KumiteRuleEngine()
        self.llm = LocalLLMClient(
            endpoint=config.model.llm_endpoint,
            model_name=config.model.llm_model_name,
            timeout_seconds=config.model.llm_timeout_seconds,
        )

        self.match = MatchContext(duration_seconds=config.match.default_duration_seconds)
        self._lock = threading.Lock()
        self._running = False
        self._default_duration_seconds = self._load_default_duration()
        self._last_runtime_state_push = 0.0
        self._timer_announcements: set[int] = set()
        self._hailo_model_path: str | None = None

        self._select_hailo_model()

    @property
    def running(self) -> bool:
        return self._running

    def run_forever(self) -> None:
        self._running = True
        self.tts.start()
        self.voice.start()
        self.vision.start()

        if not self.tts.available:
            reason = self.tts.unavailable_reason or "unknown"
            LOGGER.warning("TTS unavailable: %s", reason)
            self.storage.add_event("tts_unavailable", detail=reason)

        if not self.voice.available:
            reason = self.voice.unavailable_reason or "unknown"
            LOGGER.warning("Voice listener unavailable: %s", reason)
            self.storage.add_event("voice_unavailable", detail=reason)
            self.tts.say("Voice input is unavailable. Please check microphone and speech model setup.")

        self.storage.add_event("service_started", detail="Boston referee started")
        self.tts.say("Boston is online and ready.")

        if self.config.model.llm_required and not self.llm.is_available():
            msg = "Local language model is required but unavailable. Start the model service."
            LOGGER.error(msg)
            self.storage.add_event("llm_required_unavailable", detail=msg)
            self.tts.say(msg)

        LOGGER.info("Boston referee running")

        try:
            while self._running:
                self._process_voice()
                self._process_scoring()
                self._process_timer()
                self._publish_runtime_state()
                time.sleep(0.05)
        except KeyboardInterrupt:
            LOGGER.info("Keyboard interrupt received")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if not self._running:
            return
        LOGGER.info("Shutting down Boston referee")
        self._running = False
        self.vision.stop()
        self.voice.stop()
        self.tts.say("Boston shutting down.")
        time.sleep(0.2)
        self.tts.stop()
        self.storage.add_event("service_stopped", detail="Boston referee stopped")

    def inject_transcript(self, text: str) -> None:
        self.voice.inject_transcript(text)

    def _process_voice(self) -> None:
        transcript = self.voice.get_next_transcript(timeout=0.0)
        if not transcript:
            return

        self.storage.add_event(
            "voice_transcript",
            match_id=self.match.match_id,
            detail=transcript,
            payload={"contains_wake_word": self.intent_parser.contains_wake_word(transcript)},
        )

        if not self.intent_parser.contains_wake_word(transcript):
            return

        intent = self.intent_parser.parse(transcript)
        self._handle_intent(intent)

    def _handle_intent(self, intent: CommandIntent) -> None:
        LOGGER.info("Intent received: %s from '%s'", intent.intent, intent.raw_text)
        self.storage.add_event(
            "intent",
            match_id=self.match.match_id,
            detail=intent.intent.value,
            payload={"raw_text": intent.raw_text, "timer_seconds": intent.timer_seconds},
        )

        if intent.intent == IntentType.STATUS:
            self._respond_status()
            return

        if intent.intent == IntentType.CAMERA_CHECK:
            self._respond_camera_check()
            return

        if intent.intent == IntentType.IP_ADDRESS:
            self._respond_ip_address()
            return

        if intent.intent == IntentType.SET_TIMER:
            assert intent.timer_seconds is not None
            self._default_duration_seconds = intent.timer_seconds
            self.storage.set_setting("default_duration_seconds", str(intent.timer_seconds))
            self.tts.say(f"Timer set to {_human_duration(intent.timer_seconds)}.")
            return

        if intent.intent == IntentType.NO_TIMER:
            self._default_duration_seconds = None
            self.storage.set_setting("default_duration_seconds", "none")
            self.tts.say("Timer disabled. The next match will be untimed.")
            return

        if intent.intent == IntentType.READY:
            self._start_match_if_ready()
            return

        if intent.intent == IntentType.NEW_MATCH:
            self._handle_new_match_intent(intent)
            return

        if intent.intent == IntentType.STOP:
            self._stop_match(reason="Stopped by command")
            return

        if intent.intent == IntentType.RESTART:
            if self.match.state == MatchState.RUNNING:
                self._stop_match(reason="Restarted by command")
            self.match.reset(self._default_duration_seconds)
            self.vision.set_match_running(False)
            self.tts.say("Match state reset. Say ready when both fighters are in view.")
            return

        if intent.intent == IntentType.SPECIAL_MOVE:
            self.storage.add_event("special_move_requested", match_id=self.match.match_id, detail=intent.raw_text)
            self._play_special_move_sound()
            return

        if intent.intent == IntentType.SELF_TEST:
            self._run_self_test()
            return

        if intent.intent == IntentType.HELP:
            self.tts.say(
                "You can ask for status, camera check, self test, set timer, say ready, stop the match, or ask general questions."
            )
            return

        if intent.intent == IntentType.GENERAL_CHAT:
            prompt = self.intent_parser.remove_wake_word(intent.raw_text)
            response = self.llm.ask(
                prompt=prompt,
                system_prompt=(
                    "You are Boston, an AI karate kumite referee. Be concise, polite, and practical. "
                    "If discussing match calls, follow American points-based kumite rules."
                ),
            )
            self.tts.say(response)
            self.storage.add_event("assistant_response", match_id=self.match.match_id, detail=response)
            return

        self.tts.say("I did not catch that command. Please try again.")

    def _respond_status(self) -> None:
        vision_ready = self.vision.available and self.vision.running
        llm_ready = self.llm.is_available()
        mic_ready = self.voice.available
        speaker_ready = self.tts.running
        tts_backend = self.tts.backend
        hailo_online = bool(self._hailo_model_path)
        cam = "ready" if vision_ready else "not ready"
        llm = "ready" if llm_ready else "not ready"
        mic = "ready" if mic_ready else "not ready"
        speaker = "ready" if speaker_ready else "not ready"
        hailo = "Hailo AI accelerator online." if hailo_online else "Hailo AI accelerator not ready."
        msg = (
            f"Status check. Camera is {cam}. Model is {llm}. "
            f"Microphone is {mic}. Speaker is {speaker} using {tts_backend}. "
            f"{hailo}"
        )
        self.tts.say(msg)

    def _respond_camera_check(self) -> None:
        vis = self.vision.get_visibility()
        if vis.both_visible:
            msg = "White player OK, Blue player OK. I can see both fighters clearly."
        elif vis.white_visible and not vis.blue_visible:
            msg = "White player OK, Blue player not visible. Please adjust blue position."
        elif vis.blue_visible and not vis.white_visible:
            msg = "Blue player OK, White player not visible. Please adjust white position."
        else:
            msg = "White player not visible, Blue player not visible. Please adjust both fighters."
        self.tts.say(msg)

    def _respond_ip_address(self) -> None:
        ip = get_local_ip_address()
        if not ip:
            self.tts.say("I could not determine the Pi IP address right now.")
            self.storage.add_event("ip_address_query_failed", match_id=self.match.match_id)
            return

        spoken = " dot ".join(" ".join(list(part)) for part in ip.split("."))
        self.tts.say(f"My IP address is {spoken}.")
        self.storage.add_event(
            "ip_address_query",
            match_id=self.match.match_id,
            detail=ip,
            payload={"spoken": spoken},
        )

    def _run_self_test(self) -> None:
        self.storage.add_event("self_test_requested", match_id=self.match.match_id)
        self.tts.say("Running self test now.")

        hailo = get_hailo_status(self.config.model.hailo_status_command)
        audio_in = get_audio_input_status()
        audio_out = get_audio_output_status()
        visibility = self.vision.get_visibility()

        checks = {
            "camera": self.vision.available and self.vision.running,
            "microphone": self.voice.available and self.voice.running and bool(audio_in.get("ok")),
            "speaker": self.tts.available and self.tts.running and bool(audio_out.get("ok")),
            "llm": self.llm.is_available(),
            "hailo": bool(hailo.get("ok")) and bool(self._hailo_model_path),
        }

        lines = [
            f"Camera {'OK' if checks['camera'] else 'not ready'}.",
            f"Microphone {'OK' if checks['microphone'] else 'not ready'}.",
            f"Speaker {'OK' if checks['speaker'] else 'not ready'} using {self.tts.backend}.",
            f"Local model {'OK' if checks['llm'] else 'not ready'}.",
            "Hailo AI accelerator online." if checks["hailo"] else "Hailo AI accelerator not ready.",
        ]

        if visibility.both_visible:
            lines.append("White player OK, Blue player OK.")
        elif visibility.white_visible and not visibility.blue_visible:
            lines.append("White player OK, Blue player not visible.")
        elif visibility.blue_visible and not visibility.white_visible:
            lines.append("Blue player OK, White player not visible.")
        else:
            lines.append("White player not visible, Blue player not visible.")

        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            lines.append(f"Self test complete. Issues detected: {', '.join(failed)}.")
        else:
            lines.append("Self test complete. All core systems are ready.")

        report = " ".join(lines)
        self.storage.add_event(
            "self_test_result",
            match_id=self.match.match_id,
            detail=report,
            payload={
                "checks": checks,
                "tts_backend": self.tts.backend,
                "visibility": {
                    "white_visible": visibility.white_visible,
                    "blue_visible": visibility.blue_visible,
                },
            },
        )
        self.tts.say(report)

    def _start_match_if_ready(self) -> None:
        if self.match.state == MatchState.RUNNING:
            self.tts.say("The match is already running.")
            return

        vis = self.vision.get_visibility()
        if not vis.both_visible:
            self.tts.say("Both white and blue must be visible before starting.")
            return

        duration = self._default_duration_seconds
        with self._lock:
            self.match.reset(duration)
            self.match.state = MatchState.READY
            self.match.match_id = self.storage.start_match(duration)
            self.match.started_at = datetime.utcnow()

        if self.config.match.announce_countdown:
            self.tts.say("Three")
            self.tts.say("Two")
            self.tts.say("One")
        self.tts.say("Hajime. Fight.")

        with self._lock:
            self.match.state = MatchState.RUNNING
            self._timer_announcements.clear()

        self.vision.set_match_running(True)
        self.storage.add_event(
            "match_started",
            match_id=self.match.match_id,
            detail="Match started",
            payload={"duration_seconds": duration},
        )

    def _handle_new_match_intent(self, intent: CommandIntent) -> None:
        if self.match.state == MatchState.RUNNING:
            self.tts.say("A match is already running. Say stop the match first if you want to start a new one.")
            return

        selected_seconds = intent.timer_seconds
        if selected_seconds is None:
            self.tts.say("How long should the timer be for this match?")
            selected_seconds = self._listen_for_timer_seconds(timeout_seconds=12.0)
            if selected_seconds is None:
                self.tts.say("I did not catch a valid time. Please say start new match again and include the duration.")
                return

        self._default_duration_seconds = selected_seconds
        self.storage.set_setting("default_duration_seconds", str(selected_seconds))
        self.storage.add_event(
            "new_match_timer_selected",
            match_id=self.match.match_id,
            detail=f"{selected_seconds} seconds",
            payload={"source": "voice_dialogue"},
        )

        self.tts.say(f"Timer set to {_human_duration(selected_seconds)}. Preparing to start the match.")
        self._start_match_if_ready()

    def _listen_for_timer_seconds(self, timeout_seconds: float = 10.0) -> int | None:
        end_time = time.time() + max(1.0, timeout_seconds)
        while time.time() < end_time:
            transcript = self.voice.get_next_transcript(timeout=0.6)
            if not transcript:
                continue

            self.storage.add_event(
                "voice_transcript_timer_prompt",
                match_id=self.match.match_id,
                detail=transcript,
            )

            stripped = (
                self.intent_parser.remove_wake_word(transcript)
                if self.intent_parser.contains_wake_word(transcript)
                else transcript
            )
            if self.intent_parser.is_no_timer_request(stripped):
                return None

            parsed = self.intent_parser.parse_duration_seconds(stripped)
            if parsed is not None:
                return parsed

        return None

    def _process_scoring(self) -> None:
        if self.match.state != MatchState.RUNNING:
            return

        for candidate in self.vision.pop_candidates(limit=8):
            decision = self.rules.evaluate(candidate)
            if decision is None:
                continue

            with self._lock:
                if decision.attacker == "white":
                    self.match.white_score += decision.points
                else:
                    self.match.blue_score += decision.points
                white_score = self.match.white_score
                blue_score = self.match.blue_score
                match_id = self.match.match_id

            announcement = (
                f"Yame. {decision.attacker} scores {decision.points} "
                f"point{'s' if decision.points != 1 else ''}. "
                f"Score white {white_score}, blue {blue_score}. Hajime."
            )
            self.tts.say(announcement)
            self.storage.add_event(
                "point_awarded",
                match_id=match_id,
                actor=decision.attacker,
                points=decision.points,
                detail=decision.reason,
                payload={
                    "candidate": {
                        "attacker": candidate.attacker,
                        "target": candidate.target,
                        "technique": candidate.technique,
                        "quality": candidate.quality,
                        "timing": candidate.timing,
                        "distance": candidate.distance,
                        "control": candidate.control,
                        "source": candidate.source,
                        "metadata": candidate.metadata,
                    }
                },
            )

    def _process_timer(self) -> None:
        if self.match.state != MatchState.RUNNING:
            return
        if self.match.started_at is None or self.match.duration_seconds is None:
            return

        elapsed = (datetime.utcnow() - self.match.started_at).total_seconds()
        remaining = int(self.match.duration_seconds - elapsed)

        for checkpoint in (60, 30, 10):
            if remaining <= checkpoint and checkpoint not in self._timer_announcements:
                self._timer_announcements.add(checkpoint)
                self.tts.say(f"{checkpoint} seconds remaining.")

        if remaining <= 0:
            self._stop_match(reason="Time elapsed")

    def _stop_match(self, reason: str) -> None:
        if self.match.state != MatchState.RUNNING:
            self.tts.say("No active match to stop.")
            return

        with self._lock:
            self.match.state = MatchState.ENDED
            self.match.ended_at = datetime.utcnow()
            white_score = self.match.white_score
            blue_score = self.match.blue_score
            match_id = self.match.match_id

        self.vision.set_match_running(False)

        if white_score > blue_score:
            winner = "white"
        elif blue_score > white_score:
            winner = "blue"
        else:
            winner = "draw"

        if match_id is not None:
            self.storage.end_match(match_id, white_score, blue_score, winner)
            self.storage.add_event(
                "match_ended",
                match_id=match_id,
                detail=reason,
                payload={"winner": winner, "white_score": white_score, "blue_score": blue_score},
            )

        final_call = f"Yame. Match ended. Final score white {white_score}, blue {blue_score}. Winner: {winner}."
        self.tts.say(final_call, priority=-10)
        self.storage.add_event(
            "final_call_announced",
            match_id=match_id,
            detail=final_call,
            payload={"tts_backend": self.tts.backend},
        )

        with self._lock:
            self.match.state = MatchState.IDLE

    def _publish_runtime_state(self) -> None:
        now = time.time()
        if now - self._last_runtime_state_push < 1.0:
            return
        self._last_runtime_state_push = now

        hailo = get_hailo_status(self.config.model.hailo_status_command)
        audio_in = get_audio_input_status()
        audio_out = get_audio_output_status()
        llm_available = self.llm.is_available()
        visibility = self.vision.get_visibility()
        runtime: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "match": {
                "id": self.match.match_id,
                "state": self.match.state.value,
                "white_score": self.match.white_score,
                "blue_score": self.match.blue_score,
                "duration_seconds": self.match.duration_seconds,
                "started_at": self.match.started_at.isoformat() if self.match.started_at else None,
                "ended_at": self.match.ended_at.isoformat() if self.match.ended_at else None,
            },
            "services": {
                "camera": {"running": self.vision.running, "available": self.vision.available},
                "microphone": {
                    "running": self.voice.running,
                    "available": self.voice.available,
                    "reason": self.voice.unavailable_reason,
                },
                "speaker": {
                    "running": self.tts.running,
                    "available": self.tts.available,
                    "reason": self.tts.unavailable_reason,
                },
                "llm": {"running": llm_available, "available": llm_available},
                "hailo": hailo,
                "hailo_model": self._hailo_model_path,
                "audio_input": audio_in,
                "audio_output": audio_out,
            },
            "visibility": {
                "white_visible": visibility.white_visible,
                "blue_visible": visibility.blue_visible,
                "white_area": visibility.white_area,
                "blue_area": visibility.blue_area,
            },
            "system": get_system_stats(),
        }
        self.storage.set_runtime_state(runtime)

    def _load_default_duration(self) -> int | None:
        raw = self.storage.get_setting("default_duration_seconds")
        if raw is None:
            return self.config.match.default_duration_seconds
        if raw.lower() in {"none", "untimed", "no_timer"}:
            return None
        try:
            return int(raw)
        except ValueError:
            return self.config.match.default_duration_seconds

    def _select_hailo_model(self) -> None:
        selected = select_best_hailo_model(
            self.config.model.hailo_model_dir,
            self.config.model.hailo_preferred_models,
        )
        self._hailo_model_path = selected
        self.vision.set_hailo_model_path(selected)
        if selected:
            LOGGER.info("Selected Hailo HEF model for vision path: %s", selected)
            self.storage.add_event("hailo_model_selected", detail=selected)
        else:
            LOGGER.warning(
                "No HEF model found in %s. Vision continues in heuristic mode.",
                self.config.model.hailo_model_dir,
            )
            self.storage.add_event(
                "hailo_model_missing",
                detail=f"No HEF model found in {self.config.model.hailo_model_dir}",
            )

    def _play_special_move_sound(self) -> None:
        configured = Path(self.config.audio.special_move_sound_path)
        sound_path = configured if configured.is_absolute() else (Path.cwd() / configured)
        if not sound_path.exists():
            LOGGER.warning("Special move sound file missing: %s", sound_path)
            self.storage.add_event("special_move_failed", detail=f"missing file: {sound_path}")
            self.tts.say("Special move sound is not installed yet.")
            return

        quoted = shlex.quote(str(sound_path))
        commands = [
            f"ffplay -nodisp -autoexit -loglevel error {quoted}",
            f"mpg123 -q {quoted}",
            f"cvlc --play-and-exit --intf dummy {quoted}",
        ]

        def _play() -> None:
            for cmd in commands:
                result = run_command(cmd, timeout=20)
                if result.ok:
                    LOGGER.info("Special move sound played via command: %s", cmd)
                    self.storage.add_event("special_move_played", detail=str(sound_path), payload={"command": cmd})
                    return
            LOGGER.warning("Special move playback failed for %s", sound_path)
            self.storage.add_event("special_move_failed", detail=str(sound_path), payload={"reason": "no_player"})
            self.tts.say("Hadouken.")

        threading.Thread(target=_play, daemon=True, name="boston-special-move").start()
