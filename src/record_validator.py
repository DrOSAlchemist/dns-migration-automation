"""Validate and normalize DNS zone records before changes are applied."""

import ipaddress
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from src.record_values import normalize_rdata


SUPPORTED_TYPES = {"A", "AAAA", "CAA", "CNAME", "MX", "NS", "PTR", "SRV", "TXT"}
_LABEL_PATTERN = re.compile(r"^[a-z0-9_*](?:[a-z0-9_-]*[a-z0-9_*])?$")


def _normalize_name(name: str, zone: str) -> str:
	candidate = zone if name == "@" else name.rstrip(".").lower()
	if candidate != zone and not candidate.endswith(f".{zone}"):
		candidate = f"{candidate}.{zone}"
	labels = candidate.split(".")
	if len(candidate) > 253 or any(
		len(label) > 63 or not _LABEL_PATTERN.fullmatch(label) for label in labels
	):
		raise ValueError(f"invalid DNS name: {name!r}")
	return candidate


def validate_records(zone: str, records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
	"""Return normalized records or raise ValueError with all detected problems."""
	normalized_zone = zone.rstrip(".").lower()
	if not normalized_zone:
		raise ValueError("zone must not be empty")
	_normalize_name(normalized_zone, normalized_zone)

	normalized: list[dict[str, Any]] = []
	errors: list[str] = []
	owners: dict[str, set[str]] = defaultdict(set)
	seen: set[tuple[str, str, int, str]] = set()

	for index, record in enumerate(records):
		if not isinstance(record, Mapping):
			errors.append(f"record {index}: expected an object")
			continue
		try:
			name_value = record.get("name")
			type_value = record.get("type")
			ttl_value = record.get("ttl")
			value = record.get("value")
			if not isinstance(name_value, str) or not isinstance(type_value, str):
				raise ValueError("name and type must be strings")
			name = _normalize_name(name_value, normalized_zone)
			record_type = type_value.upper()
			if record_type not in SUPPORTED_TYPES:
				raise ValueError(f"unsupported record type: {record_type}")
			if isinstance(ttl_value, bool) or not isinstance(ttl_value, int) or ttl_value <= 0:
				raise ValueError("ttl must be a positive integer")
			if not isinstance(value, str) or not value.strip():
				raise ValueError("value must be a non-empty string")
			value = normalize_rdata(record_type, value)
			if record_type in {"A", "AAAA"}:
				parsed_value = ipaddress.ip_address(value)
				required_version = 4 if record_type == "A" else 6
				if parsed_value.version != required_version:
					raise ValueError(f"{record_type} record has an IPv{parsed_value.version} address")
				value = str(parsed_value)

			key = (name, record_type, ttl_value, value)
			if key in seen:
				raise ValueError("duplicate record")
			seen.add(key)
			owners[name].add(record_type)
			normalized.append({"name": name, "type": record_type, "ttl": ttl_value, "value": value})
		except ValueError as error:
			errors.append(f"record {index}: {error}")

	for name, record_types in owners.items():
		if "CNAME" in record_types and len(record_types) > 1:
			errors.append(f"{name}: CNAME cannot coexist with other record types")
	if errors:
		raise ValueError("Invalid DNS zone records:\n- " + "\n- ".join(errors))
	return normalized
