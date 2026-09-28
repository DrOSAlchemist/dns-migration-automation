"""Configurable TCPWave-compatible REST DNS and IPAM adapters.

TCPWave API routes vary by product version and deployment. This adapter therefore
requires explicit endpoint paths and documents a small JSON transport contract.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from src.ipam_lookup import IPAMNetwork, lookup_ip, parse_networks
from src.providers.base import DNSProviderAdapter
from src.zone_diff import ZoneDiff, group_records


class TCPWaveRESTAdapter(DNSProviderAdapter):
	def __init__(
		self,
		session: Any,
		base_url: str,
		list_path: str,
		replace_path: str,
		*,
		timeout: float = 15,
	):
		if not base_url.startswith("https://"):
			raise ValueError("TCPWave base_url must use HTTPS")
		if not list_path or not replace_path or timeout <= 0:
			raise ValueError("list_path, replace_path, and a positive timeout are required")
		self.session = session
		self.base_url = base_url.rstrip("/")
		self.list_path = list_path
		self.replace_path = replace_path
		self.timeout = timeout

	def list_records(self, zone: str) -> list[dict[str, Any]]:
		response = self.session.get(
			self._url(self.list_path, zone), timeout=self.timeout
		)
		response.raise_for_status()
		payload = response.json()
		records = payload.get("records") if isinstance(payload, dict) else payload
		if not isinstance(records, list):
			raise ValueError("TCPWave list response must be a record array or an object containing 'records'")
		return records

	def apply_diff(self, zone: str, changes: ZoneDiff) -> None:
		current = self.list_records(zone)
		current_sets = group_records(current)
		for record_set in changes.removed:
			if current_sets.get((record_set.name, record_set.record_type)) != record_set:
				raise RuntimeError(f"TCPWave record set {record_set.name} {record_set.record_type} changed after diff")
		for old, _new in changes.changed:
			if current_sets.get((old.name, old.record_type)) != old:
				raise RuntimeError(f"TCPWave record set {old.name} {old.record_type} changed after diff")
		for record_set in changes.added:
			if (record_set.name, record_set.record_type) in current_sets:
				raise RuntimeError(f"TCPWave record set {record_set.name} {record_set.record_type} appeared after diff")
		updated_records = self._apply_to_current(current, changes)
		response = self.session.put(
			self._url(self.replace_path, zone),
			json={"zone": zone, "records": updated_records},
			timeout=self.timeout,
		)
		response.raise_for_status()

	def _url(self, path: str, zone: str) -> str:
		return self.base_url + "/" + path.strip("/").format(zone=quote(zone, safe=""))

	@staticmethod
	def _apply_to_current(current: list[dict[str, Any]], changes: ZoneDiff) -> list[dict[str, Any]]:
		from src.zone_diff import diff_zone, group_records

		current_sets = group_records(current)
		for record_set in changes.removed:
			current_sets.pop((record_set.name, record_set.record_type), None)
		for old, new in changes.changed:
			current_sets.pop((old.name, old.record_type), None)
			current_sets[(new.name, new.record_type)] = new
		for record_set in changes.added:
			current_sets[(record_set.name, record_set.record_type)] = record_set
		return [record for record_set in current_sets.values() for record in record_set.as_records()]


class TCPWaveIPAMAdapter:
	"""Read-only IPAM inventory adapter over a configured JSON REST endpoint."""

	def __init__(self, session: Any, base_url: str, networks_path: str, timeout: float = 15):
		if not base_url.startswith("https://"):
			raise ValueError("TCPWave base_url must use HTTPS")
		if not networks_path or timeout <= 0:
			raise ValueError("networks_path and a positive timeout are required")
		self.session = session
		self.base_url = base_url.rstrip("/")
		self.networks_path = networks_path.strip("/")
		self.timeout = timeout

	def list_networks(self) -> list[IPAMNetwork]:
		response = self.session.get(
			f"{self.base_url}/{self.networks_path}", timeout=self.timeout
		)
		response.raise_for_status()
		payload = response.json()
		items = payload.get("networks") if isinstance(payload, dict) else payload
		if not isinstance(items, list):
			raise ValueError("TCPWave IPAM response must be a network array or an object containing 'networks'")
		return parse_networks(items)

	def lookup(self, address: str) -> IPAMNetwork | None:
		return lookup_ip(address, self.list_networks())