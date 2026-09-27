"""Locate the repository root from the installed package."""

from pathlib import Path

from shortalpha.errors import ConfigError


def project_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "migrations").is_dir():
            return candidate
    raise ConfigError("cannot locate the ShortAlpha project root")
