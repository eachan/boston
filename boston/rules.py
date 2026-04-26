from __future__ import annotations

from dataclasses import dataclass

from boston.models import ScoringCandidate, ScoringDecision


@dataclass
class KumiteThresholds:
    min_quality: float = 0.45
    min_timing: float = 0.45
    min_distance: float = 0.35
    min_control: float = 0.45


class KumiteRuleEngine:
    """Implements points-based kumite scoring for referee calls."""

    def __init__(self, thresholds: KumiteThresholds | None = None) -> None:
        self.thresholds = thresholds or KumiteThresholds()

    def evaluate(self, candidate: ScoringCandidate) -> ScoringDecision | None:
        if not self._passes_criteria(candidate):
            return None

        points = self._points_for_technique(candidate.technique)
        reason = self._build_reason(candidate, points)
        return ScoringDecision(attacker=candidate.attacker, points=points, reason=reason, candidate=candidate)

    def _passes_criteria(self, c: ScoringCandidate) -> bool:
        return (
            c.quality >= self.thresholds.min_quality
            and c.timing >= self.thresholds.min_timing
            and c.distance >= self.thresholds.min_distance
            and c.control >= self.thresholds.min_control
        )

    @staticmethod
    def _points_for_technique(technique: str) -> int:
        t = technique.lower()
        if "head_kick" in t or "spinning" in t:
            return 3
        if "body_kick" in t:
            return 2
        return 1

    @staticmethod
    def _build_reason(candidate: ScoringCandidate, points: int) -> str:
        technique_name = candidate.technique.replace("_", " ")
        return (
            f"{points} point{'s' if points != 1 else ''} for {candidate.attacker} via {technique_name} "
            f"(quality={candidate.quality:.2f}, timing={candidate.timing:.2f}, "
            f"distance={candidate.distance:.2f}, control={candidate.control:.2f})"
        )
