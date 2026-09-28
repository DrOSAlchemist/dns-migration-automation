"""Shared reconciliation contract for DNS provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from src.record_validator import validate_records
from src.zone_diff import ZoneDiff, diff_zone


def is_provider_managed(record: Mapping[str, Any], zone: str) -> bool:
	name = str(record["name"]).rstrip(".").lower()
	record_type = str(record["type"]).upper()
	return record_type == "SOA" or (record_type == "NS" and name == zone.rstrip(".").lower())


def migration_records(records: list[dict[str, Any]], zone: str) -> list[dict[str, Any]]:
	"""Remove provider-managed apex SOA/NS records from the migration state."""
	return [record for record in records if not is_provider_managed(record, zone)]


class DNSProviderAdapter(ABC):
	"""Provider base that validates desired state and applies only its computed diff."""

	@abstractmethod
	def list_records(self, zone: str) -> list[dict[str, Any]]:
		"""Return the provider-managed records in the zone as flat record objects."""

	@abstractmethod
	def apply_diff(self, zone: str, changes: ZoneDiff) -> None:
		"""Apply a precomputed diff to the named zone."""

	def replace_zone(self, zone: str, records: list[dict[str, Any]]) -> ZoneDiff:
		"""Reconcile records to validated desired state and return the applied diff."""
		desired = migration_records(validate_records(zone, records), zone)
		current = migration_records(self.list_records(zone), zone)
		changes = diff_zone(current, desired)
		if not changes.is_empty:
			self.apply_diff(zone, changes)
		return changes