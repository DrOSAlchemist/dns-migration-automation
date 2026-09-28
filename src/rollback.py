"""Persist and restore pre-migration DNS zone snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Protocol

from src.record_validator import validate_records


class DNSProvider(Protocol):
	def replace_zone(self, zone: str, records: list[dict[str, Any]]) -> None:
		"""Replace a zone's records using provider-specific safeguards."""


def save_snapshot(path: str | Path, zone: str, records: list[dict[str, Any]]) -> None:
	"""Write a validated snapshot atomically so it is safe to use for rollback."""
	document = {"zone": zone, "records": validate_records(zone, records)}
	destination = Path(path)
	destination.parent.mkdir(parents=True, exist_ok=True)
	temporary_path: str | None = None
	try:
		with tempfile.NamedTemporaryFile(
			mode="w", encoding="utf-8", dir=destination.parent, delete=False
		) as snapshot_file:
			temporary_path = snapshot_file.name
			json.dump(document, snapshot_file, indent=2)
			snapshot_file.write("\n")
		os.replace(temporary_path, destination)
	finally:
		if temporary_path and os.path.exists(temporary_path):
			os.unlink(temporary_path)


def load_snapshot(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
	"""Read and validate a snapshot without applying it to a provider."""
	with Path(path).open(encoding="utf-8") as snapshot_file:
		document = json.load(snapshot_file)
	if not isinstance(document, dict):
		raise ValueError("snapshot must be a JSON object")
	zone = document.get("zone")
	records = document.get("records")
	if not isinstance(zone, str) or not isinstance(records, list):
		raise ValueError("snapshot needs a string 'zone' and an array of 'records'")
	return zone, validate_records(zone, records)


def rollback(path: str | Path, provider: DNSProvider) -> int:
	"""Validate and restore a saved snapshot, returning the number of restored records."""
	zone, validated_records = load_snapshot(path)
	provider.replace_zone(zone, validated_records)
	return len(validated_records)
