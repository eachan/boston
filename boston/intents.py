from __future__ import annotations

import re
from dataclasses import dataclass

from boston.models import CommandIntent, IntentType


STATUS_PATTERNS = [
    r"\bare you there\b",
    r"\bare you ready\b",
    r"\bhow are you\b",
    r"\bstatus\b",
]

IP_ADDRESS_PATTERNS = [
    r"\bwhat(?:\s+is|'s)?\s+your\s+ip\s+address\b",
    r"\bip\s+address\b",
    r"\bwhat\s+is\s+the\s+pi\s+ip\b",
]

CAMERA_CHECK_PATTERNS = [
    r"\bcan you see me\b",
    r"\bcan you see us\b",
    r"\bcan you see the fighters\b",
    r"\bcheck camera\b",
]

READY_PATTERNS = [
    r"\bready\b",
    r"\blet's start\b",
    r"\bstart fight\b",
    r"\bhajime\b",
]

NEW_MATCH_PATTERNS = [
    r"\bstart (a )?new match\b",
    r"\bbegin (a )?new match\b",
    r"\bnew match\b",
    r"\bstart (the )?match\b",
    r"\bbegin (the )?match\b",
]

STOP_PATTERNS = [
    r"\bstop\b",
    r"\bstop the match\b",
    r"\bend match\b",
    r"\byame\b",
]

RESTART_PATTERNS = [
    r"\brestart\b",
    r"\breset match\b",
]

HELP_PATTERNS = [r"\bhelp\b", r"\bwhat can you do\b", r"\bcommands\b"]

SPECIAL_MOVE_PATTERNS = [
    r"\bgive me (a )?special move\b",
    r"\bspecial move\b",
    r"\bhadouken\b",
]

SELF_TEST_PATTERNS = [
    r"\bself test\b",
    r"\bcan you do (a )?self test\b",
    r"\brun (a )?self test\b",
    r"\bsystem check\b",
    r"\bdiagnostic(s)?\b",
]


@dataclass
class IntentParser:
    wake_word: str = "hey boston"

    def __post_init__(self) -> None:
        base = self._normalize(self.wake_word)
        self._wake_aliases = {
            base,
            "hey boston",
            "okay boston",
            "ok boston",
            "a boston",
            "hey baston",
            "hey boss ton",
        }

    def parse(self, text: str) -> CommandIntent:
        normalized = self._normalize(text)
        stripped = self._strip_wake_word(normalized)

        if self._matches_any(stripped, NEW_MATCH_PATTERNS):
            timer = self._extract_timer_seconds(stripped)
            return CommandIntent(intent=IntentType.NEW_MATCH, raw_text=text, timer_seconds=timer)

        timer = self._extract_timer_seconds(stripped)
        if timer is None and self._contains_no_timer(stripped):
            return CommandIntent(intent=IntentType.NO_TIMER, raw_text=text)
        if timer is not None:
            return CommandIntent(intent=IntentType.SET_TIMER, raw_text=text, timer_seconds=timer)

        if self._matches_any(stripped, STATUS_PATTERNS):
            return CommandIntent(intent=IntentType.STATUS, raw_text=text)
        if self._matches_any(stripped, IP_ADDRESS_PATTERNS):
            return CommandIntent(intent=IntentType.IP_ADDRESS, raw_text=text)
        if self._matches_any(stripped, CAMERA_CHECK_PATTERNS):
            return CommandIntent(intent=IntentType.CAMERA_CHECK, raw_text=text)
        if self._matches_any(stripped, READY_PATTERNS):
            return CommandIntent(intent=IntentType.READY, raw_text=text)
        if self._matches_any(stripped, STOP_PATTERNS):
            return CommandIntent(intent=IntentType.STOP, raw_text=text)
        if self._matches_any(stripped, RESTART_PATTERNS):
            return CommandIntent(intent=IntentType.RESTART, raw_text=text)
        if self._matches_any(stripped, SPECIAL_MOVE_PATTERNS):
            return CommandIntent(intent=IntentType.SPECIAL_MOVE, raw_text=text)
        if self._matches_any(stripped, SELF_TEST_PATTERNS):
            return CommandIntent(intent=IntentType.SELF_TEST, raw_text=text)
        if self._matches_any(stripped, HELP_PATTERNS):
            return CommandIntent(intent=IntentType.HELP, raw_text=text)
        if stripped:
            return CommandIntent(intent=IntentType.GENERAL_CHAT, raw_text=text)
        return CommandIntent(intent=IntentType.UNKNOWN, raw_text=text)

    def contains_wake_word(self, text: str) -> bool:
        normalized = self._normalize(text)
        return self._contains_any_wake_alias(normalized)

    def remove_wake_word(self, text: str) -> str:
        return self._strip_wake_word(self._normalize(text)).strip()

    def parse_duration_seconds(self, text: str) -> int | None:
        normalized = self._normalize(text)
        return self._extract_timer_seconds(normalized)

    def is_no_timer_request(self, text: str) -> bool:
        normalized = self._normalize(text)
        return self._contains_no_timer(normalized)

    def _strip_wake_word(self, normalized: str) -> str:
        for alias in sorted(self._wake_aliases, key=len, reverse=True):
            if alias and alias in normalized:
                return normalized.split(alias, 1)[1].strip(" ,.!?")
        return normalized

    def _contains_any_wake_alias(self, normalized: str) -> bool:
        for alias in self._wake_aliases:
            if alias in normalized:
                return True
        return False

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _matches_any(text: str, patterns: list[str]) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _contains_no_timer(text: str) -> bool:
        return bool(re.search(r"\b(no time|untimed|without time|no timer)\b", text))

    @staticmethod
    def _extract_timer_seconds(text: str) -> int | None:
        number_words = {
            "zero": 0,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
        }

        half_numeric = re.search(r"\b(\d{1,2})\s+(?:and\s+)?(?:a\s+)?half\s+minutes?\b", text)
        if half_numeric:
            return int(half_numeric.group(1)) * 60 + 30

        half_word = re.search(
            r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
            r"(?:and\s+)?(?:a\s+)?half\s+minutes?\b",
            text,
        )
        if half_word:
            return number_words[half_word.group(1)] * 60 + 30

        minute_match = re.search(r"\b(\d{1,2})\s*(minute|minutes|min)\b", text)
        second_match = re.search(r"\b(\d{1,3})\s*(second|seconds|sec)\b", text)
        if minute_match:
            return int(minute_match.group(1)) * 60
        if second_match:
            return int(second_match.group(1))

        word_minute = re.search(
            r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+minutes?\b",
            text,
        )
        if word_minute:
            return number_words[word_minute.group(1)] * 60

        word_second = re.search(
            r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+seconds?\b",
            text,
        )
        if word_second:
            return number_words[word_second.group(1)]

        compact_match = re.search(r"\bset timer (to )?(\d{1,3})\b", text)
        if compact_match:
            # Default ambiguous timer values under 10 to minutes, otherwise seconds.
            value = int(compact_match.group(2))
            return value * 60 if value <= 10 else value
        return None
