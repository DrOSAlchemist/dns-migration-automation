"""Load, validate, and hand off DNS zones to a provider adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from src.record_validator import validate_records


class DNSProvider(Protocol):
	def replace_zone(self, zone: str, records: list[dict[str, Any]]) -> None:
		"""Replace a zone's records, using provider-specific transactional safeguards."""


def load_zone(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
	"""Load a JSON zone document and validate every record before returning it."""
	with Path(path).open(encoding="utf-8") as zone_file:
		document = json.load(zone_file)
	if not isinstance(document, dict):
		raise ValueError("zone document must be a JSON object")
	zone = document.get("zone")
	records = document.get("records")
	if not isinstance(zone, str) or not isinstance(records, list):
		raise ValueError("zone document needs a string 'zone' and an array of 'records'")
	return zone, validate_records(zone, records)


def import_zone(path: str | Path, provider: DNSProvider) -> int:
	"""Validate a zone file, replace the provider zone, and return the record count."""
	zone, records = load_zone(path)
	provider.replace_zone(zone, records)
	return len(records)
