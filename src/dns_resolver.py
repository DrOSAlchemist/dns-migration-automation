"""DNS resolver adapter for propagation checks, backed by dnspython."""

from __future__ import annotations

from typing import Any, Callable


def make_resolver(nameserver: str, timeout: float = 3) -> Callable[[str, str, str], list[str]]:
	"""Create a resolver callable pinned to one nameserver, with no search suffixes."""
	try:
		import dns.resolver
	except ImportError as error:
		raise RuntimeError("Install DNS resolver dependencies with: pip install -e .") from error
	if timeout <= 0:
		raise ValueError("timeout must be positive")
	resolver = dns.resolver.Resolver(configure=False)
	resolver.nameservers = [nameserver]
	resolver.timeout = timeout
	resolver.lifetime = timeout

	def resolve(name: str, record_type: str, _label: str) -> list[str]:
		try:
			answer = resolver.resolve(name.rstrip(".") + ".", record_type, search=False)
		except (
			dns.resolver.NXDOMAIN,
			dns.resolver.NoAnswer,
			dns.resolver.NoNameservers,
			dns.resolver.LifetimeTimeout,
		):
			return []
		return [_format_answer(record_type.upper(), record) for record in answer]

	return resolve


def _format_answer(record_type: str, record: Any) -> str:
	if record_type in {"A", "AAAA"}:
		return record.address
	if record_type in {"CNAME", "NS", "PTR"}:
		return record.target.to_text().rstrip(".").lower()
	if record_type == "MX":
		return f"{record.preference} {record.exchange.to_text().rstrip('.').lower()}"
	if record_type == "SRV":
		return f"{record.priority} {record.weight} {record.port} {record.target.to_text().rstrip('.').lower()}"
	if record_type == "CAA":
		return f"{record.flags} {record.tag.lower()} {record.value.decode('utf-8')}"
	if record_type == "TXT":
		return b"".join(record.strings).decode("utf-8")
	return record.to_text().strip('"')