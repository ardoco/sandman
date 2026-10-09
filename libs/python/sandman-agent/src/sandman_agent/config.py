"""Settings from SANDMAN_* environment variables; *_FILE variables are read from files."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[5] / "contracts"  # repository checkout


class ConfigError(Exception):
    """Missing or invalid configuration."""


LLM_MODES = ("live", "record", "replay")


def llm_mode(env: Mapping[str, str] | None = None) -> str:
    """SANDMAN_LLM_MODE (live|record|replay, default live); anything else is a ConfigError."""
    mode = (os.environ if env is None else env).get("SANDMAN_LLM_MODE") or "live"
    if mode not in LLM_MODES:
        raise ConfigError(f"invalid SANDMAN_LLM_MODE {mode!r}, expected one of {', '.join(LLM_MODES)}")
    return mode


@dataclass(frozen=True)
class Settings:
    """Runtime configuration of one service (Stage 2 §1.7)."""

    service_name: str
    contracts_dir: Path = DEFAULT_CONTRACTS_DIR
    llm_base_url: str | None = None
    llm_key: str | None = None
    llm_local_key: str | None = None
    llm_models_file: Path | None = None
    kg_uri: str | None = None
    kg_user: str | None = None
    kg_password: str | None = None
    nats_url: str | None = None
    service_token: str | None = None
    service_verify: Mapping[str, str] = field(default_factory=dict)  # caller -> sha256(token)
    plugin_inbound_token: str | None = None
    artifact_store: Path | None = None
    config_dir: Path | None = None
    log_file: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Read settings; fails with all missing names if SANDMAN_SERVICE_NAME is absent."""
        env = os.environ if env is None else env

        def value(name: str) -> str | None:
            if f"{name}_FILE" in env:
                try:
                    return Path(env[f"{name}_FILE"]).read_text(encoding="utf-8").strip()
                except OSError as e:
                    raise ConfigError(f"cannot read {name}_FILE={env[f'{name}_FILE']}: {e}") from e
            return env.get(name) or None

        def path(name: str) -> Path | None:
            v = env.get(name)
            return Path(v) if v else None

        name = env.get("SANDMAN_SERVICE_NAME")
        if not name:
            raise ConfigError("SANDMAN_SERVICE_NAME is required")
        verify_file = path("SANDMAN_SERVICE_VERIFY_FILE")
        try:
            verify = json.loads(verify_file.read_text(encoding="utf-8")) if verify_file else {}
        except (OSError, ValueError) as e:
            raise ConfigError(f"cannot read SANDMAN_SERVICE_VERIFY_FILE={verify_file}: {e}") from e
        return cls(
            service_name=name,
            contracts_dir=path("SANDMAN_CONTRACTS_DIR") or DEFAULT_CONTRACTS_DIR,
            llm_base_url=env.get("SANDMAN_LLM_BASE_URL") or None,
            llm_key=value("SANDMAN_LLM_KEY"),
            llm_local_key=value("SANDMAN_LLM_LOCAL_KEY"),
            llm_models_file=path("SANDMAN_LLM_MODELS_FILE"),
            kg_uri=env.get("SANDMAN_KG_URI") or None,
            kg_user=env.get("SANDMAN_KG_USER") or None,
            kg_password=value("SANDMAN_KG_PASSWORD"),
            nats_url=env.get("SANDMAN_NATS_URL") or None,
            service_token=value("SANDMAN_SERVICE_TOKEN"),
            service_verify=verify,
            plugin_inbound_token=value("SANDMAN_PLUGIN_INBOUND_TOKEN"),
            artifact_store=path("SANDMAN_ARTIFACT_STORE"),
            config_dir=path("SANDMAN_CONFIG_DIR"),
            log_file=path("SANDMAN_LOG_FILE"),
        )

    def require(self, *names: str) -> None:
        """Fail with one message listing every required setting that is unset."""
        known = {f.name for f in fields(self)}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"unknown settings: {unknown}")
        missing = [n for n in names if getattr(self, n) in (None, "", {})]
        if missing:
            env_names = ", ".join("SANDMAN_" + n.upper() for n in missing)
            raise ConfigError(f"{self.service_name}: missing configuration {env_names}")
