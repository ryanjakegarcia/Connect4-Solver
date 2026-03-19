from dataclasses import dataclass
from typing import Optional


@dataclass
class AutoRuntimeState:
    control_state: str
    runtime_start_at: float
    runtime_limit_sec: Optional[float]
    runtime_hard_limit_sec: Optional[float]
    timeout_triggered: bool
    hard_timeout_triggered: bool
    auto_quit_after_drain: bool
    exit_requested_after_drain: bool
    timeout_summary_printed: bool

    @classmethod
    def from_args(cls, *, mode: str, auto_max_runtime_sec: float, now: float) -> "AutoRuntimeState":
        runtime_limit_sec = (
            float(auto_max_runtime_sec)
            if mode == "auto" and auto_max_runtime_sec and auto_max_runtime_sec > 0
            else None
        )
        runtime_hard_limit_sec = runtime_limit_sec * 2.0 if runtime_limit_sec is not None else None
        return cls(
            control_state="running",
            runtime_start_at=now if mode == "auto" else 0.0,
            runtime_limit_sec=runtime_limit_sec,
            runtime_hard_limit_sec=runtime_hard_limit_sec,
            timeout_triggered=False,
            hard_timeout_triggered=False,
            auto_quit_after_drain=False,
            exit_requested_after_drain=False,
            timeout_summary_printed=False,
        )
