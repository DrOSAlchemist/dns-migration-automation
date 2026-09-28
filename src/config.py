"""Environment configuration loading with explicit environment-variable expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def _expand(value: Any) -> Any:
	if isinstance(value, str):
		def replace(match: re.Match[str]) -> str:
			name, default = match.groups()
			if name in os.environ:
				return os.environ[name]
			if default is not None:
				return default
			return match.group(0)

		return _ENV_PATTERN.sub(replace, value)
	if isinstance(value, list):
		return [_expand(item) for item in value]
	if isinstance(value, dict):
		return {key: _expand(item) for key, item in value.items()}
	return value


def load_environment(path: str | Path, environment: str) -> dict[str, Any]:
	"""Read one environment mapping from YAML and expand ``${VAR}`` references."""
	try:
		import yaml
	except ImportError as error:
		raise RuntimeError("Install the project first with: pip install -e .") from error
	with Path(path).open(encoding="utf-8") as config_file:
		config = yaml.safe_load(config_file) or {}
	if not isinstance(config, dict) or not isinstance(config.get("environments", {}), dict):
		raise ValueError("configuration must contain an 'environments' mapping")
	environments = config.get("environments", {})
	if environment not in environments:
		raise ValueError(f"environment {environment!r} is not defined in {path}")
	settings = _expand(environments[environment])
	if not isinstance(settings, dict):
		raise ValueError(f"environment {environment!r} must be a mapping")
	return settings