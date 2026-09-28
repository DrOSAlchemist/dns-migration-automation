"""Construct configured provider adapters with each vendor's standard credential chain."""

from __future__ import annotations

from typing import Any, Mapping

from src.providers.azure_dns import AzureDNSAdapter
from src.providers.cloud_dns import CloudDNSAdapter
from src.providers.route53 import Route53Adapter
from src.providers.tcpwave import TCPWaveIPAMAdapter, TCPWaveRESTAdapter


def _required_setting(config: Mapping[str, Any], name: str) -> str:
	value = config.get(name)
	if not isinstance(value, str) or not value or "${" in value:
		raise ValueError(f"provider setting {name!r} is missing or has an unresolved environment variable")
	return value


def _create_tcpwave_session(config: Mapping[str, Any]) -> Any:
	try:
		import requests
		from requests.adapters import HTTPAdapter
		from urllib3.util.retry import Retry
	except ImportError as error:
		raise RuntimeError("Install TCPWave dependencies with: pip install -e '.[tcpwave]'") from error
	session = requests.Session()
	session.verify = config.get("verify_tls", True)
	token = config.get("token")
	if isinstance(token, str) and "${" in token:
		raise ValueError("TCPWave token environment variable is not set")
	if token:
		session.headers["Authorization"] = "Bearer " + str(token)
	if config.get("username") and config.get("password"):
		if "${" in str(config["username"]) or "${" in str(config["password"]):
			raise ValueError("TCPWave basic-auth environment variables are not set")
		session.auth = (str(config["username"]), str(config["password"]))
	elif config.get("username") or config.get("password"):
		raise ValueError("TCPWave basic authentication requires both username and password")
	retry = Retry(
		total=3,
		backoff_factor=0.4,
		status_forcelist=(429, 500, 502, 503, 504),
		allowed_methods=frozenset({"GET"}),
	)
	session.mount("https://", HTTPAdapter(max_retries=retry))
	return session


def build_provider(config: Mapping[str, Any]) -> Any:
	"""Build one DNS adapter from its environment mapping."""
	provider = str(config.get("provider", "")).lower()
	_required_setting(config, "zone")
	if provider in {"route53", "aws"}:
		try:
			import boto3
		except ImportError as error:
			raise RuntimeError("Install AWS dependencies with: pip install -e '.[aws]'") from error
		client = boto3.client("route53", region_name=config.get("region"))
		return Route53Adapter(client, _required_setting(config, "hosted_zone_id"))
	if provider in {"cloud-dns", "google-cloud-dns", "gcp"}:
		try:
			from google.cloud import dns
		except ImportError as error:
			raise RuntimeError("Install Google Cloud DNS dependencies with: pip install -e '.[gcp]'") from error
		client = dns.Client(project=_required_setting(config, "project_id"))
		return CloudDNSAdapter(client, _required_setting(config, "managed_zone"), _required_setting(config, "zone"))
	if provider in {"azure-dns", "azure"}:
		try:
			from azure.identity import DefaultAzureCredential
			from azure.mgmt.dns import DnsManagementClient
		except ImportError as error:
			raise RuntimeError("Install Azure DNS dependencies with: pip install -e '.[azure]'") from error
		credential = DefaultAzureCredential()
		client = DnsManagementClient(credential, _required_setting(config, "subscription_id"))
		return AzureDNSAdapter(client, _required_setting(config, "resource_group"), _required_setting(config, "zone"))
	if provider == "tcpwave":
		session = _create_tcpwave_session(config)
		return TCPWaveRESTAdapter(
			session,
			_required_setting(config, "base_url"),
			_required_setting(config, "list_path"),
			_required_setting(config, "replace_path"),
			timeout=float(config.get("timeout", 15)),
		)
	raise ValueError("provider must be route53, cloud-dns, azure-dns, or tcpwave")


def build_ipam_adapter(config: Mapping[str, Any]) -> TCPWaveIPAMAdapter:
	"""Build the supported read-only TCPWave IPAM REST adapter."""
	if str(config.get("provider", "")).lower() != "tcpwave":
		raise ValueError("IPAM provider must be tcpwave")
	session = _create_tcpwave_session(config)
	return TCPWaveIPAMAdapter(
		session,
		_required_setting(config, "base_url"),
		_required_setting(config, "networks_path"),
		timeout=float(config.get("timeout", 15)),
	)