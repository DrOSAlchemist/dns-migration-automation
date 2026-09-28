"""Google Cloud DNS adapter using an injected google-cloud-dns client."""

from __future__ import annotations

from typing import Any

from src.providers.base import DNSProviderAdapter, migration_records
from src.record_values import encode_provider_rdata, normalize_rdata
from src.zone_diff import RecordSet, ZoneDiff


class CloudDNSAdapter(DNSProviderAdapter):
	def __init__(self, client: Any, managed_zone: str, dns_name: str):
		if not managed_zone or not dns_name:
			raise ValueError("managed_zone and dns_name are required")
		self.client = client
		self.managed_zone = managed_zone
		self.dns_name = dns_name.rstrip(".") + "."

	def _get_zone(self) -> Any:
		zone = self.client.zone(self.managed_zone, dns_name=self.dns_name)
		zone.reload()
		return zone

	def list_records(self, zone: str) -> list[dict[str, Any]]:
		cloud_zone = self._get_zone()
		flat_records = []
		for record_set in cloud_zone.list_resource_record_sets():
			name = record_set.name.rstrip(".").lower()
			record_type = record_set.record_type.upper()
			if record_type == "SOA" or (record_type == "NS" and name == zone.rstrip(".").lower()):
				continue
			if not hasattr(record_set, "ttl") or not hasattr(record_set, "rrdatas"):
				raise NotImplementedError(f"Cloud DNS record {name} {record_type} cannot be represented safely")
			for value in record_set.rrdatas:
				flat_records.append({
					"name": name,
					"type": record_type,
					"ttl": int(record_set.ttl),
					"value": normalize_rdata(record_type, str(value)),
				})
		return migration_records(flat_records, zone)

	def apply_diff(self, zone: str, changes: ZoneDiff) -> None:
		cloud_zone = self._get_zone()
		change = cloud_zone.changes()
		for record_set in changes.removed:
			change.delete_record_set(self._to_google(cloud_zone, record_set))
		for old, new in changes.changed:
			change.delete_record_set(self._to_google(cloud_zone, old))
			change.add_record_set(self._to_google(cloud_zone, new))
		for record_set in changes.added:
			change.add_record_set(self._to_google(cloud_zone, record_set))
		change.create()

	@staticmethod
	def _to_google(cloud_zone: Any, record_set: RecordSet) -> Any:
		return cloud_zone.resource_record_set(
			record_set.name + ".",
			record_set.record_type,
			record_set.ttl,
			[
				encode_provider_rdata(record_set.record_type, value)
				for value in record_set.values
			],
		)