"""Find the most-specific IPAM network containing an IP address."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from typing import Iterable, Mapping, Union


IPAddress = Union[IPv4Address, IPv6Address]
IPNetwork = Union[IPv4Network, IPv6Network]


@dataclass(frozen=True)
class IPAMNetwork:
	network: IPNetwork
	metadata: Mapping[str, str]


def lookup_ip(address: str | IPAddress, entries: Iterable[IPAMNetwork]) -> IPAMNetwork | None:
	"""Return the matching network with the longest prefix, if any."""
	parsed_address = ip_address(address)
	matches = [
		entry
		for entry in entries
		if parsed_address.version == entry.network.version and parsed_address in entry.network
	]
	return max(matches, key=lambda entry: entry.network.prefixlen, default=None)


def parse_networks(items: Iterable[Mapping[str, object]]) -> list[IPAMNetwork]:
	"""Build IPAM entries from mappings containing ``network`` and optional ``metadata``."""
	entries = []
	for item in items:
		network_value = item.get("network")
		metadata_value = item.get("metadata", {})
		if not isinstance(network_value, str) or not isinstance(metadata_value, Mapping):
			raise ValueError("each IPAM entry needs a network string and metadata mapping")
		metadata = {str(key): str(value) for key, value in metadata_value.items()}
		entries.append(IPAMNetwork(ip_network(network_value, strict=False), metadata))
	return entries
