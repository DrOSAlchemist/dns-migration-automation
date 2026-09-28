import unittest
from types import SimpleNamespace

from src.dns_resolver import _format_answer
from src.providers.azure_dns import AzureDNSAdapter
from src.record_values import encode_provider_rdata, normalize_rdata
from src.record_validator import validate_records


class RecordValueTests(unittest.TestCase):
	def test_txt_presentation_round_trips_through_provider_format(self):
		self.assertEqual(encode_provider_rdata("TXT", "migration=ready"), '"migration=ready"')
		self.assertEqual(normalize_rdata("TXT", '"migration=ready"'), "migration=ready")
		self.assertEqual(normalize_rdata("TXT", '"first" "second"'), "firstsecond")

	def test_target_names_normalize_trailing_dots(self):
		self.assertEqual(normalize_rdata("CNAME", "WWW.Example.COM."), "www.example.com")
		self.assertEqual(normalize_rdata("MX", "10 Mail.Example.COM."), "10 mail.example.com")
		self.assertEqual(normalize_rdata("SRV", "1 2 443 Target.Example.COM."), "1 2 443 target.example.com")

	def test_validator_stores_txt_as_logical_text_and_checks_mx_format(self):
		records = validate_records("example.com", [
			{"name": "@", "type": "TXT", "ttl": 60, "value": '"hello world"'},
		])
		self.assertEqual(records[0]["value"], "hello world")
		with self.assertRaisesRegex(ValueError, "MX value"):
			validate_records("example.com", [
				{"name": "@", "type": "MX", "ttl": 60, "value": "mail.example.com"},
			])

	def test_resolver_answers_match_canonical_values(self):
		target = SimpleNamespace(to_text=lambda: "Mail.Example.COM.")
		self.assertEqual(_format_answer("CNAME", SimpleNamespace(target=target)), "mail.example.com")
		self.assertEqual(_format_answer("MX", SimpleNamespace(preference=10, exchange=target)), "10 mail.example.com")
		self.assertEqual(
			_format_answer("CAA", SimpleNamespace(flags=0, tag="ISSUE", value=b"ca.example")),
			"0 issue ca.example",
		)
		self.assertEqual(AzureDNSAdapter._from_model("NS", SimpleNamespace(nsdname="ns1.example.com.")), "ns1.example.com.")
		self.assertEqual(AzureDNSAdapter._to_model("NS", "ns1.example.com"), {"nsdname": "ns1.example.com"})


if __name__ == "__main__":
	unittest.main()