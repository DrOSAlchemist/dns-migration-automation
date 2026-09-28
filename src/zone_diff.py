"""Canonical record-set grouping and desired-state diffing."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, order=True)
class RecordSet:
	name: str
	record_type: str
	ttl: int
	values: tuple[str, ...]

	def as_records(self) -> list[dict[str, Any]]:
		return [
			{"name": self.name, "type": self.record_type, "ttl": self.ttl, "value": value}
			for value in self.values
		]


@dataclass(frozen=True)
class ZoneDiff:
	added: tuple[RecordSet, ...]
	removed: tuple[RecordSet, ...]
	changed: tuple[tuple[RecordSet, RecordSet], ...]

	@property
	def is_empty(self) -> bool:
		return not (self.added or self.removed or self.changed)


def group_records(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], RecordSet]:
	"""Group flat records into provider-neutral record sets, enforcing one TTL per set."""
	grouped: dict[tuple[str, str], tuple[int, set[str]]] = {}
	for record in records:
		name = str(record["name"]).rstrip(".").lower()
		record_type = str(record["type"]).upper()
		ttl = record["ttl"]
		value = str(record["value"])
		if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
			raise ValueError(f"{name} {record_type}: TTL must be a positive integer")
		key = (name, record_type)
		if key in grouped:
			prior_ttl, values = grouped[key]
			if prior_ttl != ttl:
				raise ValueError(f"{name} {record_type}: one record set cannot have multiple TTL values")
			values.add(value)
		else:
			grouped[key] = (ttl, {value})
	return {
		key: RecordSet(key[0], key[1], ttl, tuple(sorted(values)))
		for key, (ttl, values) in grouped.items()
	}


def diff_zone(
	current: Iterable[Mapping[str, Any]], desired: Iterable[Mapping[str, Any]]
) -> ZoneDiff:
	"""Compare current and desired flat records, including full record-set changes."""
	current_sets = group_records(current)
	desired_sets = group_records(desired)
	added = []
	removed = []
	changed = []
	for key in sorted(current_sets.keys() | desired_sets.keys()):
		old = current_sets.get(key)
		new = desired_sets.get(key)
		if old is None:
			added.append(new)
		elif new is None:
			removed.append(old)
		elif old != new:
			changed.append((old, new))
	return ZoneDiff(tuple(added), tuple(removed), tuple(changed))