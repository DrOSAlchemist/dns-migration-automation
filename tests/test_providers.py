from types import SimpleNamespace
import unittest

from src.providers.azure_dns import AzureDNSAdapter
from src.providers.cloud_dns import CloudDNSAdapter
from src.providers.route53 import Route53Adapter
from src.providers.tcpwave import TCPWaveIPAMAdapter, TCPWaveRESTAdapter
from src.zone_diff import diff_zone


class FakeResponse:
	def __init__(self, payload=None):
		self.payload = payload

	def raise_for_status(self):
		return None

	def json(self):
		return self.payload


class ProviderAdapterTests(unittest.TestCase):
	def test_route53_lists_and_replaces_record_sets(self):
		class Paginator:
			def paginate(self, **_kwargs):
				return [{"ResourceRecordSets": [
					{"Name": "example.com.", "Type": "SOA", "TTL": 900, "ResourceRecords": [{"Value": "ns.example.com."}]},
					{"Name": "www.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.1"}]},
				]}]

		class Client:
			def __init__(self):
				self.batches = []

			def get_paginator(self, _operation):
				return Paginator()

			def change_resource_record_sets(self, **kwargs):
				self.batches.append(kwargs)

		client = Client()
		adapter = Route53Adapter(client, "Z123")
		changes = adapter.replace_zone("example.com", [
			{"name": "www", "type": "A", "ttl": 60, "value": "192.0.2.2"},
		])

		self.assertEqual(changes.changed[0][0].values, ("192.0.2.1",))
		self.assertEqual(len(client.batches), 1)
		operations = client.batches[0]["ChangeBatch"]["Changes"]
		self.assertEqual([operation["Action"] for operation in operations], ["DELETE", "CREATE"])
		self.assertEqual(operations[1]["ResourceRecordSet"]["TTL"], 60)

	def test_route53_refuses_to_flatten_alias_records(self):
		class Paginator:
			def paginate(self, **_kwargs):
				return [{"ResourceRecordSets": [{
					"Name": "www.example.com.",
					"Type": "A",
					"AliasTarget": {"DNSName": "target.example.net."},
				}]}]

		class Client:
			def get_paginator(self, _operation):
				return Paginator()

		with self.assertRaisesRegex(NotImplementedError, "alias or policy"):
			Route53Adapter(Client(), "Z123").list_records("example.com")

	def test_cloud_dns_uses_atomic_changes(self):
		class CloudRecordSet:
			def __init__(self, name, record_type, ttl, rrdatas):
				self.name = name
				self.record_type = record_type
				self.ttl = ttl
				self.rrdatas = rrdatas

		class Change:
			def __init__(self):
				self.additions = []
				self.deletions = []
				self.created = False

			def add_record_set(self, record_set):
				self.additions.append(record_set)

			def delete_record_set(self, record_set):
				self.deletions.append(record_set)

			def create(self):
				self.created = True

		class Zone:
			def __init__(self):
				self.change = Change()

			def reload(self):
				return None

			def list_resource_record_sets(self):
				return [CloudRecordSet("www.example.com.", "A", 300, ["192.0.2.1"])]

			def changes(self):
				return self.change

			def resource_record_set(self, name, record_type, ttl, values):
				return CloudRecordSet(name, record_type, ttl, values)

		class Client:
			def __init__(self):
				self.cloud_zone = Zone()

			def zone(self, *_args, **_kwargs):
				return self.cloud_zone

		client = Client()
		adapter = CloudDNSAdapter(client, "example-zone", "example.com")
		adapter.replace_zone("example.com", [
			{"name": "www", "type": "A", "ttl": 300, "value": "192.0.2.2"},
		])

		self.assertTrue(client.cloud_zone.change.created)
		self.assertEqual(len(client.cloud_zone.change.deletions), 1)
		self.assertEqual(client.cloud_zone.change.additions[0].rrdatas, ["192.0.2.2"])

	def test_azure_dns_serializes_mx_record(self):
		class Model:
			def __init__(self, **kwargs):
				self.__dict__.update(kwargs)

		class RecordSets:
			def __init__(self):
				self.created = []

			def list_all_by_dns_zone(self, *_args):
				return []

			def create_or_update(self, *args):
				self.created.append(args)

			def delete(self, *_args):
				return None

		models = SimpleNamespace(
			RecordSet=Model,
			ARecord=Model,
			AaaaRecord=Model,
			CaaRecord=Model,
			CnameRecord=Model,
			MxRecord=Model,
			NsRecord=Model,
			PtrRecord=Model,
			SrvRecord=Model,
			TxtRecord=Model,
		)
		record_sets = RecordSets()
		client = SimpleNamespace(record_sets=record_sets)
		adapter = AzureDNSAdapter(client, "rg-dns", "example.com", models)
		adapter.replace_zone("example.com", [
			{"name": "mail", "type": "MX", "ttl": 300, "value": "10 mx.example.com"},
		])

		call = record_sets.created[0]
		self.assertEqual(call[2:4], ("mail", "MX"))
		self.assertEqual(call[4].mx_records[0].preference, 10)
		self.assertEqual(call[4].mx_records[0].exchange, "mx.example.com")

	def test_tcpwave_rest_reconciles_records_and_ipam_lookup(self):
		class Session:
			def __init__(self):
				self.records = [{"name": "www.example.com", "type": "A", "ttl": 300, "value": "192.0.2.1"}]
				self.puts = []

			def get(self, _url, **_kwargs):
				return FakeResponse({"records": self.records})

			def put(self, _url, **kwargs):
				self.puts.append(kwargs)
				self.records = kwargs["json"]["records"]
				return FakeResponse({})

		session = Session()
		adapter = TCPWaveRESTAdapter(session, "https://tcpwave.example", "zones/{zone}/records", "zones/{zone}/records")
		adapter.replace_zone("example.com", [
			{"name": "www", "type": "A", "ttl": 60, "value": "192.0.2.2"},
		])
		self.assertEqual(session.puts[0]["json"]["records"][0]["value"], "192.0.2.2")

		session.get = lambda *_args, **_kwargs: FakeResponse({"networks": [
			{"network": "192.0.2.0/24", "metadata": {"site": "west"}},
		]})
		ipam = TCPWaveIPAMAdapter(session, "https://tcpwave.example", "api/ipam/networks")
		self.assertEqual(ipam.lookup("192.0.2.18").metadata["site"], "west")

	def test_tcpwave_requires_https(self):
		with self.assertRaisesRegex(ValueError, "HTTPS"):
			TCPWaveRESTAdapter(object(), "http://tcpwave.example", "list", "replace")

	def test_tcpwave_rejects_concurrent_record_set_changes(self):
		class Session:
			def __init__(self):
				self.records = [{"name": "www.example.com", "type": "A", "ttl": 300, "value": "192.0.2.1"}]
				self.put_called = False

			def get(self, _url, **_kwargs):
				return FakeResponse({"records": self.records})

			def put(self, *_args, **_kwargs):
				self.put_called = True
				return FakeResponse({})

		session = Session()
		adapter = TCPWaveRESTAdapter(session, "https://tcpwave.example", "zones/{zone}/records", "zones/{zone}/records")
		changes = diff_zone(session.records, [
			{"name": "www.example.com", "type": "A", "ttl": 60, "value": "192.0.2.2"},
		])
		session.records = [{"name": "www.example.com", "type": "A", "ttl": 60, "value": "192.0.2.3"}]

		with self.assertRaisesRegex(RuntimeError, "changed after diff"):
			adapter.apply_diff("example.com", changes)
		self.assertFalse(session.put_called)


if __name__ == "__main__":
	unittest.main()