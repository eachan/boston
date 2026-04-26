from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field
import yaml


class AudioConfig(BaseModel):
    sample_rate: int = 16_000
    channels: int = 1
    blocksize: int = 4_000
    vosk_model_path: str = "models/vosk"
    wake_word: str = "hey boston"
    special_move_sound_path: str = "hadouken.mp3"
    input_gain: float = 1.6
    use_command_grammar: bool = True
    partial_min_words: int = 2
    command_phrases: list[str] = Field(
        default_factory=lambda: [
            "hey boston",
            "okay boston",
            "status",
            "what is your ip address",
            "what s your ip address",
            "ip address",
            "camera check",
            "can you see us",
            "can you see the fighters",
            "ready",
            "start new match",
            "begin new match",
            "start match",
            "begin match",
            "stop the match",
            "restart",
            "set timer",
            "no timer",
            "self test",
            "special move",
            "hadouken",
            "one minute",
            "two minutes",
            "three minutes",
            "one and a half minutes",
            "ninety seconds",
            "white",
            "blue",
        ]
    )


class TTSConfig(BaseModel):
    enabled: bool = True
    backend: str = "auto"
    rate: int = 155
    volume: float = 1.0
    preferred_voice_tokens: list[str] = Field(
        default_factory=lambda: ["female", "f3", "en-us", "english-us", "us"]
    )
    fallback_voice_id: str | None = "english-us+f3"
    piper_command: str = ".venv/bin/piper"
    piper_model_path: str = "models/piper/en_US-lessac-medium.onnx"
    piper_model_config_path: str = "models/piper/en_US-lessac-medium.onnx.json"


class VisionConfig(BaseModel):
    camera_index: int = 0
    frame_width: int = 1280
    frame_height: int = 720
    fps: int = 30
    detection_interval_seconds: float = 0.2
    min_color_area: int = 8_500


class ModelConfig(BaseModel):
    llm_endpoint: str = "http://127.0.0.1:11434/api/generate"
    llm_model_name: str = "llama3.2:3b"
    llm_timeout_seconds: int = 15
    llm_required: bool = False
    hailo_status_command: str = "hailortcli fw-control identify"
    hailo_model_dir: str = "/usr/share/hailo-models"
    hailo_preferred_models: list[str] = Field(
        default_factory=lambda: [
            "yolov8s_pose_h8.hef",
            "yolov8s_pose_h8l_pi.hef",
            "yolov8m_pose_h10.hef",
            "yolov8s_h8.hef",
            "yolov6n_h8.hef",
        ]
    )


class MatchConfig(BaseModel):
    default_duration_seconds: int = 120
    announce_countdown: bool = True


class DashboardConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    refresh_seconds: int = 2


class ServiceConfig(BaseModel):
    referee_service_name: str = "boston-referee"
    dashboard_service_name: str = "boston-dashboard"
    model_service_name: str = "ollama"
    managed_services: list[str] = Field(
        default_factory=lambda: ["boston-referee", "boston-dashboard", "ollama"]
    )


class AppConfig(BaseModel):
    app_name: str = "Boston"
    data_dir: str = "data"
    database_path: str = "data/boston.db"
    log_path: str = "data/boston.log"
    audio: AudioConfig = Field(default_factory=AudioConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    match: MatchConfig = Field(default_factory=MatchConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    services: ServiceConfig = Field(default_factory=ServiceConfig)


DEFAULT_CONFIG_PATH = Path("config/boston.yaml")


def load_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return AppConfig(**data)
    return AppConfig()
