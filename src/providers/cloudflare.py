"""Cloudflare DNS adapter for simple, unproxied DNS records."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.providers.base import DNSProviderAdapter, migration_records
from src.record_values import encode_provider_rdata, normalize_rdata
from src.zone_diff import RecordSet, ZoneDiff, diff_zone, group_records


Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], Mapping[str, Any]]


def _urlopen_transport(
	method: str,
	url: str,
	headers: Mapping[str, str],
	body: bytes | None,
	timeout: float,
) -> Mapping[str, Any]:
	request = Request(url, data=body, headers=dict(headers), method=method)
	try:
		with urlopen(request, timeout=timeout) as response:
			payload = response.read()
	except HTTPError as error:
		payload = error.read()
		try:
			body_message = json.loads(payload).get("errors", [])
		except (ValueError, AttributeError):
			body_message = payload.decode("utf-8", errors="replace")[:500]
		raise RuntimeError(f"Cloudflare API returned HTTP {error.code}: {body_message}") from error
	except URLError as error:
		raise RuntimeError(f"Cloudflare API request failed: {error.reason}") from error
	if not payload:
		return {"success": True, "result": None}
	try:
		return json.loads(payload)
	except ValueError as error:
		raise RuntimeError("Cloudflare API returned invalid JSON") from error


class CloudflareAdapter(DNSProviderAdapter):
	"""Reconcile simple unproxied records in one Cloudflare zone.

	The adapter intentionally refuses to rewrite records with Cloudflare-specific
	proxy, comment, tag, setting, private-routing, or glue metadata.
	"""

	API_ROOT = "https://api.cloudflare.com/client/v4"
	SUPPORTED_TYPES = {"A", "AAAA", "CAA", "CNAME", "MX", "NS", "PTR", "SRV", "TXT"}

	def __init__(
		self,
		zone_id: str,
		zone_name: str,
		api_token: str,
		*,
		timeout: float = 15,
		transport: Transport | None = None,
	):
		if not zone_id:
			raise ValueError("Cloudflare zone_id is required")
		if not zone_name or not zone_name.strip().strip("."):
			raise ValueError("Cloudflare zone_name is required")
		if not api_token or "${" in api_token:
			raise ValueError("Cloudflare API token is missing or unresolved")
		if timeout <= 0:
			raise ValueError("timeout must be positive")
		self.zone_id = zone_id
		self.zone_name = zone_name.strip().strip(".").lower()
		self.api_token = api_token
		self.timeout = timeout
		self.transport = transport or _urlopen_transport
		self._record_ids: dict[tuple[str, str, int, str], str] = {}

	def _request(self, method: str, path: str, *, query: Mapping[str, Any] | None = None,
				 payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
		url = f"{self.API_ROOT}{path}"
		if query:
			url += "?" + urlencode(query)
		body = json.dumps(payload).encode("utf-8") if payload is not None else None
		response = self.transport(
			method,
			url,
			{
				"Authorization": f"Bearer {self.api_token}",
				"Accept": "application/json",
				"Content-Type": "application/json",
			},
			body,
			self.timeout,
		)
		if response.get("success") is not True:
			errors = response.get("errors") or response.get("messages") or "unknown API error"
			raise RuntimeError(f"Cloudflare API request failed: {errors}")
		return response

	def list_records(self, zone: str) -> list[dict[str, Any]]:
		self._require_zone(zone)
		all_records = []
		page = 1
		total_pages = 1
		self._record_ids = {}
		while page <= total_pages:
			response = self._request(
				"GET",
				f"/zones/{self.zone_id}/dns_records",
				query={"page": page, "per_page": 100},
			)
			results = response.get("result")
			if not isinstance(results, list):
				raise RuntimeError("Cloudflare list response is missing its result array")
			for record in results:
				flat_record = self._to_flat_record(record, zone)
				if flat_record is None:
					continue
				all_records.append(flat_record)
				key = self._record_key(flat_record)
				record_id = record.get("id")
				if not isinstance(record_id, str) or not record_id:
					raise RuntimeError(f"Cloudflare record {key[0]} {key[1]} has no record ID")
				self._record_ids[key] = record_id
			page_info = response.get("result_info") or {}
			total_pages = page_info.get("total_pages", page)
			if isinstance(total_pages, bool) or not isinstance(total_pages, int) or total_pages < page:
				total_pages = page
			page += 1
		return migration_records(all_records, zone)

	def apply_diff(self, zone: str, changes: ZoneDiff) -> None:
		self._require_zone(zone)
		current = self.list_records(zone)
		current_sets = group_records(current)
		for old in changes.removed:
			self._assert_unchanged(old, current_sets)
		for old, _new in changes.changed:
			self._assert_unchanged(old, current_sets)
		for added in changes.added:
			if (added.name, added.record_type) in current_sets:
				raise RuntimeError(f"Cloudflare record set {added.name} {added.record_type} appeared after diff")

		for record_set in changes.removed:
			self._delete_record_set(record_set)
		for old, _new in changes.changed:
			self._delete_record_set(old)
		for record_set in changes.added:
			self._create_record_set(record_set)
		for _old, record_set in changes.changed:
			self._create_record_set(record_set)

	def _delete_record_set(self, record_set: RecordSet) -> None:
		for record in record_set.as_records():
			key = self._record_key(record)
			record_id = self._record_ids.get(key)
			if not record_id:
				raise RuntimeError(f"Cloudflare record ID missing for {key[0]} {key[1]}; refusing deletion")
			self._request("DELETE", f"/zones/{self.zone_id}/dns_records/{record_id}")

	def _create_record_set(self, record_set: RecordSet) -> None:
		for value in record_set.values:
			payload = self._to_cloudflare_payload(record_set, value)
			self._request("POST", f"/zones/{self.zone_id}/dns_records", payload=payload)

	def _to_flat_record(self, record: Mapping[str, Any], zone: str) -> dict[str, Any] | None:
		name_value = record.get("name")
		record_type_value = record.get("type")
		if not isinstance(name_value, str) or not isinstance(record_type_value, str):
			raise RuntimeError("Cloudflare returned a record without a name or type")
		name = name_value.rstrip(".").lower()
		record_type = record_type_value.upper()
		zone_name = zone.rstrip(".").lower()
		if record_type == "SOA" or (record_type == "NS" and name == zone_name):
			return None
		if name != zone_name and not name.endswith("." + zone_name):
			raise RuntimeError(f"Cloudflare returned out-of-zone record {name!r}")
		if record_type not in self.SUPPORTED_TYPES:
			raise NotImplementedError(f"Cloudflare record type {record_type} is not supported; refusing reconciliation")
		self._reject_advanced_metadata(record)
		ttl = record.get("ttl")
		if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
			raise RuntimeError(f"Cloudflare returned an invalid TTL for {name} {record_type}")
		value = self._record_value(record, record_type)
		return {"name": name, "type": record_type, "ttl": ttl, "value": normalize_rdata(record_type, value)}

	@staticmethod
	def _reject_advanced_metadata(record: Mapping[str, Any]) -> None:
		if record.get("proxied"):
			raise NotImplementedError("Cloudflare proxied records are not supported; refusing reconciliation")
		if record.get("comment") or record.get("tags") or record.get("private_routing"):
			raise NotImplementedError("Cloudflare record comments, tags, and private routing are not supported")
		if record.get("settings"):
			raise NotImplementedError("Cloudflare record settings are not supported; refusing reconciliation")
		meta = record.get("meta")
		if isinstance(meta, Mapping) and any(value for value in meta.values()):
			raise NotImplementedError("Cloudflare glue or shadowed record metadata is not supported")

	@staticmethod
	def _record_value(record: Mapping[str, Any], record_type: str) -> str:
		content = record.get("content")
		if not isinstance(content, str):
			raise RuntimeError(f"Cloudflare {record_type} record has no content")
		if record_type == "MX":
			priority = record.get("priority")
			if isinstance(priority, bool) or not isinstance(priority, int):
				raise RuntimeError("Cloudflare MX record has no numeric priority")
			return f"{priority} {content}"
		if record_type == "CAA":
			data = record.get("data")
			if isinstance(data, Mapping) and all(key in data for key in ("flags", "tag", "value")):
				return f"{data['flags']} {data['tag']} {data['value']}"
		if record_type == "SRV":
			data = record.get("data")
			if isinstance(data, Mapping) and all(key in data for key in ("priority", "weight", "port", "target")):
				return f"{data['priority']} {data['weight']} {data['port']} {data['target']}"
		return content

	@staticmethod
	def _to_cloudflare_payload(record_set: RecordSet, value: str) -> dict[str, Any]:
		payload: dict[str, Any] = {
			"name": record_set.name,
			"type": record_set.record_type,
			"ttl": record_set.ttl,
			"proxied": False,
		}
		record_type = record_set.record_type
		if record_type == "MX":
			priority, content = value.split(None, 1)
			payload.update({"priority": int(priority), "content": content})
		elif record_type == "CAA":
			flags, tag, caa_value = value.split(None, 2)
			payload["data"] = {"flags": int(flags), "tag": tag, "value": caa_value}
		elif record_type == "SRV":
			priority, weight, port, target = value.split(None, 3)
			payload["data"] = {
				"priority": int(priority),
				"weight": int(weight),
				"port": int(port),
				"target": target,
			}
		else:
			payload["content"] = encode_provider_rdata(record_type, value)
		return payload

	@staticmethod
	def _record_key(record: Mapping[str, Any]) -> tuple[str, str, int, str]:
		return (
			str(record["name"]).rstrip(".").lower(),
			str(record["type"]).upper(),
			int(record["ttl"]),
			normalize_rdata(str(record["type"]), str(record["value"])),
		)

	@staticmethod
	def _assert_unchanged(record_set: RecordSet, current_sets: Mapping[tuple[str, str], RecordSet]) -> None:
		key = (record_set.name, record_set.record_type)
		if current_sets.get(key) != record_set:
			raise RuntimeError(f"Cloudflare record set {record_set.name} {record_set.record_type} changed after diff")

	def _require_zone(self, zone: str) -> None:
		if zone.rstrip(".").lower() != self.zone_name:
			raise ValueError(f"configured Cloudflare zone {self.zone_name!r} does not match {zone!r}")