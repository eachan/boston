from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MatchState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    ENDED = "ended"


class IntentType(str, Enum):
    STATUS = "status"
    IP_ADDRESS = "ip_address"
    CAMERA_CHECK = "camera_check"
    READY = "ready"
    NEW_MATCH = "new_match"
    STOP = "stop"
    RESTART = "restart"
    SET_TIMER = "set_timer"
    NO_TIMER = "no_timer"
    HELP = "help"
    SPECIAL_MOVE = "special_move"
    SELF_TEST = "self_test"
    GENERAL_CHAT = "general_chat"
    UNKNOWN = "unknown"


@dataclass
class CommandIntent:
    intent: IntentType
    raw_text: str
    timer_seconds: int | None = None


@dataclass
class FighterVisibility:
    white_visible: bool
    blue_visible: bool
    white_area: int = 0
    blue_area: int = 0

    @property
    def both_visible(self) -> bool:
        return self.white_visible and self.blue_visible


@dataclass
class ScoringCandidate:
    attacker: str
    target: str
    technique: str
    quality: float
    timing: float
    distance: float
    control: float
    source: str = "vision"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoringDecision:
    attacker: str
    points: int
    reason: str
    candidate: ScoringCandidate


@dataclass
class MatchContext:
    match_id: int | None = None
    state: MatchState = MatchState.IDLE
    white_score: int = 0
    blue_score: int = 0
    duration_seconds: int | None = 120
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def reset(self, duration_seconds: int | None) -> None:
        self.match_id = None
        self.state = MatchState.IDLE
        self.white_score = 0
        self.blue_score = 0
        self.duration_seconds = duration_seconds
        self.started_at = None
        self.ended_at = None
