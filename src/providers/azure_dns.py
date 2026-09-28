"""Azure DNS adapter using an injected DnsManagementClient and model namespace."""

from __future__ import annotations

from typing import Any

from src.providers.base import DNSProviderAdapter, migration_records
from src.record_values import normalize_rdata
from src.zone_diff import RecordSet, ZoneDiff


class AzureDNSAdapter(DNSProviderAdapter):
	_SUPPORTED_MODEL_FIELDS = {
		"A": ("a_records", "ARecord"),
		"AAAA": ("aaaa_records", "AaaaRecord"),
		"CAA": ("caa_records", "CaaRecord"),
		"CNAME": ("cname_record", "CnameRecord"),
		"MX": ("mx_records", "MxRecord"),
		"NS": ("ns_records", "NsRecord"),
		"PTR": ("ptr_records", "PtrRecord"),
		"SRV": ("srv_records", "SrvRecord"),
		"TXT": ("txt_records", "TxtRecord"),
	}

	def __init__(self, client: Any, resource_group: str, zone_name: str, models: Any = None):
		if not resource_group or not zone_name:
			raise ValueError("resource_group and zone_name are required")
		if models is None:
			try:
				from azure.mgmt.dns import models as azure_models
			except ImportError as error:
				raise RuntimeError("Install the Azure extra with: pip install '.[azure]'") from error
			models = azure_models
		self.client = client
		self.resource_group = resource_group
		self.zone_name = zone_name.rstrip(".")
		self.models = models

	def list_records(self, zone: str) -> list[dict[str, Any]]:
		if zone.rstrip(".").lower() != self.zone_name.lower():
			raise ValueError(f"configured Azure zone {self.zone_name!r} does not match {zone!r}")
		flat_records = []
		for record_set in self.client.record_sets.list_all_by_dns_zone(
			self.resource_group, self.zone_name
		):
			name = self._fqdn(record_set.name, zone)
			record_type = str(record_set.type).rsplit("/", 1)[-1].upper()
			if record_type == "SOA" or (record_type == "NS" and name == zone.rstrip(".").lower()):
				continue
			if record_type not in self._SUPPORTED_MODEL_FIELDS:
				raise NotImplementedError(f"Azure DNS record type {record_type} is not supported yet")
			field_name, _ = self._SUPPORTED_MODEL_FIELDS[record_type]
			record_values = getattr(record_set, field_name, None)
			if record_type == "CNAME":
				record_values = [record_values] if record_values else []
			for record_value in record_values or []:
				value = self._from_model(record_type, record_value)
				flat_records.append({
					"name": name,
					"type": record_type,
					"ttl": int(record_set.ttl),
					"value": normalize_rdata(record_type, str(value)),
				})
		return migration_records(flat_records, zone)

	def apply_diff(self, zone: str, changes: ZoneDiff) -> None:
		if zone.rstrip(".").lower() != self.zone_name.lower():
			raise ValueError(f"configured Azure zone {self.zone_name!r} does not match {zone!r}")
		for record_set in changes.removed:
			self._delete(zone, record_set)
		for old, new in changes.changed:
			self._delete(zone, old)
			self._upsert(zone, new)
		for record_set in changes.added:
			self._upsert(zone, record_set)

	def _delete(self, zone: str, record_set: RecordSet) -> None:
		self.client.record_sets.delete(
			self.resource_group,
			self.zone_name,
			self._relative_name(record_set.name, zone),
			record_set.record_type,
		)

	def _upsert(self, zone: str, record_set: RecordSet) -> None:
		record_type = record_set.record_type
		field_name, model_name = self._SUPPORTED_MODEL_FIELDS[record_type]
		model_class = getattr(self.models, model_name)
		values = [model_class(**self._to_model(record_type, value)) for value in record_set.values]
		if record_type == "CNAME":
			parameters = self.models.RecordSet(ttl=record_set.ttl, **{field_name: values[0]})
		else:
			parameters = self.models.RecordSet(ttl=record_set.ttl, **{field_name: values})
		self.client.record_sets.create_or_update(
			self.resource_group,
			self.zone_name,
			self._relative_name(record_set.name, zone),
			record_type,
			parameters,
		)

	@staticmethod
	def _from_model(record_type: str, record: Any) -> str:
		if record_type == "A":
			return record.ipv4_address
		if record_type == "AAAA":
			return record.ipv6_address
		if record_type == "CAA":
			return f"{record.flags} {record.tag} {record.value}"
		if record_type == "CNAME":
			return record.cname
		if record_type == "MX":
			return f"{record.preference} {record.exchange}"
		if record_type == "NS":
			return record.nsdname
		if record_type == "PTR":
			return record.ptrdname
		if record_type == "SRV":
			return f"{record.priority} {record.weight} {record.port} {record.target}"
		value = record.value
		return "".join(value) if isinstance(value, list) else value

	@staticmethod
	def _to_model(record_type: str, value: str) -> dict[str, Any]:
		if record_type == "A":
			return {"ipv4_address": value}
		if record_type == "AAAA":
			return {"ipv6_address": value}
		if record_type == "CAA":
			flags, tag, caa_value = value.split(None, 2)
			return {"flags": int(flags), "tag": tag, "value": caa_value.strip('"')}
		if record_type == "CNAME":
			return {"cname": value}
		if record_type == "MX":
			preference, exchange = value.split(None, 1)
			return {"preference": int(preference), "exchange": exchange}
		if record_type == "NS":
			return {"nsdname": value}
		if record_type == "PTR":
			return {"ptrdname": value}
		if record_type == "SRV":
			priority, weight, port, target = value.split(None, 3)
			return {
				"priority": int(priority),
				"weight": int(weight),
				"port": int(port),
				"target": target,
			}
		return {"value": [value[index:index + 255] for index in range(0, len(value), 255)]}

	@staticmethod
	def _relative_name(name: str, zone: str) -> str:
		name = name.rstrip(".").lower()
		zone = zone.rstrip(".").lower()
		if name == zone:
			return "@"
		if not name.endswith("." + zone):
			raise ValueError(f"record {name!r} is outside zone {zone!r}")
		return name[: -(len(zone) + 1)]

	@staticmethod
	def _fqdn(name: str, zone: str) -> str:
		if name in {"", "@"}:
			return zone.rstrip(".").lower()
		name = name.rstrip(".").lower()
		if name == zone.rstrip(".").lower() or name.endswith("." + zone.rstrip(".").lower()):
			return name
		return name + "." + zone.rstrip(".").lower()