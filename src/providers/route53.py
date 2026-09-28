"""Amazon Route 53 adapter using an injected boto3 Route 53 client."""

from __future__ import annotations

from typing import Any

from src.providers.base import DNSProviderAdapter, migration_records
from src.record_values import encode_provider_rdata, normalize_rdata
from src.zone_diff import RecordSet, ZoneDiff


class Route53Adapter(DNSProviderAdapter):
	def __init__(self, client: Any, hosted_zone_id: str):
		if not hosted_zone_id:
			raise ValueError("hosted_zone_id is required")
		self.client = client
		self.hosted_zone_id = hosted_zone_id

	def list_records(self, zone: str) -> list[dict[str, Any]]:
		paginator = self.client.get_paginator("list_resource_record_sets")
		response_records = []
		for page in paginator.paginate(HostedZoneId=self.hosted_zone_id):
			response_records.extend(page.get("ResourceRecordSets", []))

		flat_records = []
		for record_set in response_records:
			name = record_set["Name"].rstrip(".").lower()
			record_type = record_set["Type"].upper()
			if record_type == "SOA" or (record_type == "NS" and name == zone.rstrip(".").lower()):
				continue
			if "AliasTarget" in record_set or record_set.get("SetIdentifier"):
				raise NotImplementedError(
					f"Route 53 alias or policy record {name} {record_type} is not supported; refusing reconciliation"
				)
			if "TTL" not in record_set or "ResourceRecords" not in record_set:
				raise NotImplementedError(f"Route 53 record {name} {record_type} cannot be represented safely")
			for resource_record in record_set["ResourceRecords"]:
				flat_records.append({
					"name": name,
					"type": record_type,
					"ttl": int(record_set["TTL"]),
					"value": normalize_rdata(record_type, resource_record["Value"]),
				})
		return migration_records(flat_records, zone)

	def apply_diff(self, zone: str, changes: ZoneDiff) -> None:
		operations = []
		for record_set in changes.removed:
			operations.append({"Action": "DELETE", "ResourceRecordSet": self._to_aws(record_set)})
		for old, new in changes.changed:
			operations.append({"Action": "DELETE", "ResourceRecordSet": self._to_aws(old)})
			operations.append({"Action": "CREATE", "ResourceRecordSet": self._to_aws(new)})
		for record_set in changes.added:
			operations.append({"Action": "CREATE", "ResourceRecordSet": self._to_aws(record_set)})
		for offset in range(0, len(operations), 1000):
			self.client.change_resource_record_sets(
				HostedZoneId=self.hosted_zone_id,
				ChangeBatch={
					"Comment": f"DNS migration reconcile for {zone}",
					"Changes": operations[offset:offset + 1000],
				},
			)

	@staticmethod
	def _to_aws(record_set: RecordSet) -> dict[str, Any]:
		return {
			"Name": record_set.name + ".",
			"Type": record_set.record_type,
			"TTL": record_set.ttl,
			"ResourceRecords": [
				{"Value": encode_provider_rdata(record_set.record_type, value)}
				for value in record_set.values
			],
		}