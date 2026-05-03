"""PTT FCFS state machine — DISCORD vs RADIO holder."""
import time
from enum import Enum


class Holder(Enum):
    IDLE = "idle"
    RADIO = "radio"
    DISCORD = "discord"


class PttState:
    def __init__(self, idle_release_ms: int = 800):
        self._holder = Holder.IDLE
        self._last_activity = 0.0
        self._idle_release_s = idle_release_ms / 1000

    @property
    def holder(self) -> Holder:
        if (self._holder is not Holder.IDLE
                and time.monotonic() - self._last_activity >= self._idle_release_s):
            self._holder = Holder.IDLE
        return self._holder

    def acquire(self, who: Holder) -> bool:
        h = self.holder
        if h is who:
            self._last_activity = time.monotonic()
            return True
        if h is Holder.IDLE:
            self._holder = who
            self._last_activity = time.monotonic()
            return True
        return False

    def release(self, who: Holder) -> None:
        if self._holder is who:
            self._holder = Holder.IDLE

    def touch(self, who: Holder) -> None:
        if self._holder is who:
            self._last_activity = time.monotonic()
