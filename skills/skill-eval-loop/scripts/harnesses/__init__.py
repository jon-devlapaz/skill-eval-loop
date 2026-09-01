"""Harness adapters used by skill_eval_loop."""

from __future__ import annotations

from harnesses.antigravity import AntigravityAdapter
from harnesses.base import BaseHarnessAdapter, TraceResult, is_infrastructure_failure
from harnesses.claude import ClaudeAdapter
from harnesses.codex import CodexAdapter, parse_trace, prepare_run_codex_home, trace_value
from harnesses.cursor_agent import CursorAgentAdapter
from harnesses.hermes import HermesAdapter
from harnesses.muse import MuseAdapter
from harnesses.pi import PiAdapter
from harnesses.script import ScriptAdapter

SUPPORTED_HARNESSES: dict[str, type[BaseHarnessAdapter]] = {
    "antigravity": AntigravityAdapter,
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "cursor-agent": CursorAgentAdapter,
    "hermes": HermesAdapter,
    "muse": MuseAdapter,
    "pi": PiAdapter,
    "script": ScriptAdapter,
}


def get_harness_adapter(name: str) -> BaseHarnessAdapter:
    adapter_class = SUPPORTED_HARNESSES.get(name)
    if not adapter_class:
        supported = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise ValueError(f"unsupported harness {name!r}; supported harnesses are: {supported}")
    return adapter_class()


def resolve_harness(executable_or_name: str, executable: str | None = None) -> tuple[str, str]:
    if executable is None and executable_or_name not in SUPPORTED_HARNESSES:
        adapter = get_harness_adapter("codex")
        return adapter.resolve(executable_or_name)
    adapter = get_harness_adapter(executable_or_name)
    return adapter.resolve(executable)


__all__ = [
    "BaseHarnessAdapter",
    "SUPPORTED_HARNESSES",
    "TraceResult",
    "get_harness_adapter",
    "is_infrastructure_failure",
    "parse_trace",
    "prepare_run_codex_home",
    "resolve_harness",
    "trace_value",
]
