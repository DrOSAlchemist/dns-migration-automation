"""Normalize common DNS RDATA between operator input and provider wire formats."""

from __future__ import annotations

import shlex


def normalize_rdata(record_type: str, value: str) -> str:
	"""Normalize TXT and domain-valued RDATA into the project's flat record format."""
	record_type = record_type.upper()
	value = value.strip()
	if record_type == "TXT":
		return decode_txt(value)
	if record_type in {"CNAME", "NS", "PTR"}:
		return value.rstrip(".").lower()
	if record_type == "MX":
		parts = value.split()
		if len(parts) != 2:
			raise ValueError("MX value must be '<preference> <exchange>'")
		return f"{int(parts[0])} {parts[1].rstrip('.').lower()}"
	if record_type == "SRV":
		parts = value.split()
		if len(parts) != 4:
			raise ValueError("SRV value must be '<priority> <weight> <port> <target>'")
		return f"{int(parts[0])} {int(parts[1])} {int(parts[2])} {parts[3].rstrip('.').lower()}"
	if record_type == "CAA":
		parts = value.split(None, 2)
		if len(parts) != 3:
			raise ValueError("CAA value must be '<flags> <tag> <value>'")
		caa_value = parts[2].strip('"')
		return f"{int(parts[0])} {parts[1].lower()} {caa_value}"
	return value


def encode_provider_rdata(record_type: str, value: str) -> str:
	"""Encode TXT text as DNS presentation-format RDATA for cloud SDKs."""
	if record_type.upper() != "TXT":
		return normalize_rdata(record_type, value)
	text = decode_txt(value)
	return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def decode_txt(value: str) -> str:
	"""Decode one or more quoted TXT presentation strings into their logical value."""
	value = value.strip()
	if not value.startswith('"'):
		return value
	try:
		chunks = shlex.split(value, posix=True)
	except ValueError as error:
		raise ValueError(f"invalid TXT presentation value: {error}") from error
	return "".join(chunks)