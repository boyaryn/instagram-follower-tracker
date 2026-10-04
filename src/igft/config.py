"""Configuration: TOML file plus IGFT_* environment overrides, validated at startup."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import timedelta
from pathlib import Path
from typing import Any

import platformdirs

from igft.domain import IgftError

APP_NAME = "igft"
CONFIG_ENV = "IGFT_CONFIG"
ENV_PREFIX = "IGFT_"


class ConfigError(IgftError):
    pass


def _dirs() -> platformdirs.PlatformDirs:
    # appauthor=False keeps Windows paths at %LOCALAPPDATA%\igft rather than igft\igft.
    return platformdirs.PlatformDirs(APP_NAME, appauthor=False)


def default_config_path() -> Path:
    return Path(_dirs().user_config_dir) / "config.toml"


def default_sessions_dir() -> Path:
    return Path(_dirs().user_data_dir) / "sessions"


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    instaloader_session_path: Path
    firefox_profile: Path | None
    instaloader_user_agent: str | None
    delay_min_seconds: float
    delay_max_seconds: float
    page_cap: int
    early_stop_pages: int
    cooldown_hours: float

    @property
    def cooldown(self) -> timedelta:
        return timedelta(hours=self.cooldown_hours)


def default_values() -> dict[str, Any]:
    sessions = default_sessions_dir()
    return {
        "database_url": None,
        "instaloader_session_path": sessions / "instaloader.session",
        "firefox_profile": None,
        "instaloader_user_agent": None,
        "delay_min_seconds": 15.0,
        "delay_max_seconds": 45.0,
        "page_cap": 300,
        "early_stop_pages": 2,
        "cooldown_hours": 24.0,
    }


def _to_path(value: Any) -> Path:
    return Path(str(value)).expanduser()


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"expected a number, got {value!r}")
    return float(value)


def _to_int(value: Any) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"expected a whole number, got {value!r}")
    return int(value)


def _to_optional_str(value: Any) -> str | None:
    text = str(value).strip()
    return text or None


def _to_optional_path(value: Any) -> Path | None:
    return _to_path(value) if str(value).strip() else None


_CONVERTERS: dict[str, Callable[[Any], Any]] = {
    "database_url": _to_optional_str,
    "instaloader_session_path": _to_path,
    "firefox_profile": _to_optional_path,
    "instaloader_user_agent": _to_optional_str,
    "delay_min_seconds": _to_float,
    "delay_max_seconds": _to_float,
    "page_cap": _to_int,
    "early_stop_pages": _to_int,
    "cooldown_hours": _to_float,
}

SETTING_NAMES: tuple[str, ...] = tuple(f.name for f in fields(Settings))


def env_name(setting: str) -> str:
    return ENV_PREFIX + setting.upper()


def _config_file(config_path: Path | None, env: Mapping[str, str]) -> tuple[Path, bool]:
    """Return the file to read and whether it must exist."""
    if config_path is not None:
        return Path(config_path).expanduser(), True
    if env.get(CONFIG_ENV):
        return Path(env[CONFIG_ENV]).expanduser(), True
    return default_config_path(), False


def _read_toml(path: Path, required: bool) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise ConfigError(f"Config file not found: {path}")
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid TOML: {exc}") from exc
    unknown = sorted(set(data) - set(SETTING_NAMES))
    if unknown:
        raise ConfigError(
            f"Unknown setting(s) in {path}: {', '.join(unknown)}. Valid settings: {', '.join(SETTING_NAMES)}"
        )
    return data


def load_settings(config_path: Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from the config file and IGFT_* environment variables, then validate them.

    The file is `config_path` if given, else `$IGFT_CONFIG`, else the per-user default
    (which may be absent). Environment variables override values from the file.
    """
    env = os.environ if env is None else env
    path, required = _config_file(config_path, env)
    raw: dict[str, Any] = dict(_read_toml(path, required))
    for name in SETTING_NAMES:
        if env_name(name) in env:
            raw[name] = env[env_name(name)]

    values = default_values()
    problems: list[str] = []
    for name, value in raw.items():
        try:
            values[name] = _CONVERTERS[name](value)
        except (TypeError, ValueError) as exc:
            problems.append(f"{name}: {exc}")
    settings = Settings(**values)
    problems.extend(validate(settings))
    if problems:
        raise ConfigError("Invalid configuration:\n  " + "\n  ".join(problems))
    return settings


def validate(settings: Settings) -> list[str]:
    problems = []
    if settings.delay_min_seconds < 0 or settings.delay_max_seconds < 0:
        problems.append("delay_min_seconds and delay_max_seconds must not be negative")
    if settings.delay_min_seconds > settings.delay_max_seconds:
        problems.append(
            f"delay_min_seconds ({settings.delay_min_seconds:g}) must not be greater than "
            f"delay_max_seconds ({settings.delay_max_seconds:g})"
        )
    if settings.page_cap < 1:
        problems.append("page_cap must be at least 1")
    if settings.early_stop_pages < 1:
        problems.append("early_stop_pages must be at least 1")
    if settings.cooldown_hours <= 0:
        problems.append("cooldown_hours must be greater than 0")
    return problems
