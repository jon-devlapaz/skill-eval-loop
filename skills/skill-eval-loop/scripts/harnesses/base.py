"""Shared harness adapter contract and empty-trace helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Any


@dataclass(frozen=True)
class ModelListing:
    models: tuple[str, ...] = ()
    source: str = "unavailable"


INFRASTRUCTURE_FAILURE_MARKERS = (
    "failed to lookup address information",
    "error sending request",
    "connection refused",
    "connection reset",
    "network is unreachable",
    "econnrefused",
    "etimedout",
)


@dataclass
class TraceResult:
    response: str = ""
    actual_model: str = ""
    session_id: str = ""
    skill_accessed: bool = False
    failure_message: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def noop_env() -> tuple[dict[str, str], Path | None]:
    return {}, None


def isolated_home(
    output_dir: Path, name: str, env_key: str
) -> tuple[dict[str, str], Path]:
    home = output_dir / name
    home.mkdir(parents=True, exist_ok=True)
    return {env_key: str(home)}, home


def is_infrastructure_failure(message: str) -> bool:
    lowered = message.casefold()
    return any(marker in lowered for marker in INFRASTRUCTURE_FAILURE_MARKERS)


class BaseHarnessAdapter:
    name: str = ""
    default_executable: str = ""

    def resolve(self, executable: str | None) -> tuple[str, str]:
        target = executable or self.default_executable
        if not target:
            raise ValueError(f"executable is required for harness {self.name!r}")
        resolved = shutil.which(target)
        if resolved is None:
            path = Path(target).resolve()
            if path.is_file():
                resolved = str(path)
            else:
                raise ValueError(f"{self.name} executable not found: {target}")
        try:
            version = subprocess.run(
                [resolved, "--version"], text=True, capture_output=True, check=True
            ).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            version = f"{self.name} 1.0"
        if not version:
            version = f"{self.name} 1.0"
        return resolved, version

    def prepare_environment(self, output_dir: Path) -> tuple[dict[str, str], Path | None]:
        return noop_env()

    def invocation_env(self, invocation_dir: Path) -> dict[str, str]:
        return {}

    def cleanup_environment(self, home_dir: Path | None) -> None:
        if home_dir is not None and home_dir.exists():
            shutil.rmtree(home_dir, ignore_errors=True)

    def build_command(
        self,
        *,
        executable: str,
        model: str,
        prompt: str,
        workspace: Path,
        role: str,
        timeout_seconds: int,
        skill_name: str = "",
    ) -> list[str]:
        raise NotImplementedError

    def parse_trace(
        self,
        trace_path: Path,
        stderr_path: Path,
        skill_name: str = "",
    ) -> dict[str, Any]:
        text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        return TraceResult(response=text.strip()).as_dict()

    def is_infrastructure_failure(self, message: str) -> bool:
        return is_infrastructure_failure(message)

    def list_models(self, executable: str) -> ModelListing:
        return ModelListing()


def reject_unknown_model(listing: ModelListing, model: str, label: str, harness: str) -> None:
    if not listing.models:
        return
    if model in listing.models:
        return
    available = ", ".join(listing.models)
    raise ValueError(
        f"{label} {model!r} is not available on harness {harness!r}; "
        f"available models ({listing.source}): {available}"
    )
