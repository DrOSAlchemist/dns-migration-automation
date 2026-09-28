import json
from pathlib import Path
import tempfile
import unittest

from src.ipam_lookup import lookup_ip, parse_networks
from src.propagation_checker import check_propagation
from src.record_validator import validate_records
from src.rollback import rollback, save_snapshot
from src.zone_import import import_zone, load_zone


class FakeProvider:
    def __init__(self):
        self.calls = []

    def replace_zone(self, zone, records):
        self.calls.append((zone, records))


class DNSAutomationTests(unittest.TestCase):
    def test_lookup_ip_returns_longest_matching_prefix(self):
        entries = parse_networks([
            {"network": "10.0.0.0/8", "metadata": {"site": "wide"}},
            {"network": "10.2.0.0/16", "metadata": {"site": "specific"}},
            {"network": "2001:db8::/32", "metadata": {"site": "ipv6"}},
        ])

        self.assertEqual(lookup_ip("10.2.4.5", entries).metadata["site"], "specific")
        self.assertEqual(lookup_ip("2001:db8::1", entries).metadata["site"], "ipv6")
        self.assertIsNone(lookup_ip("192.0.2.5", entries))

    def test_validate_records_normalizes_names_and_addresses(self):
        records = validate_records("Example.COM.", [
            {"name": "www", "type": "a", "ttl": 300, "value": "192.0.2.10"},
            {"name": "@", "type": "TXT", "ttl": 60, "value": "migration=ready"},
        ])

        self.assertEqual(records[0]["name"], "www.example.com")
        self.assertEqual(records[0]["type"], "A")
        self.assertEqual(records[1]["name"], "example.com")

    def test_validate_records_rejects_invalid_address_and_cname_collision(self):
        with self.assertRaisesRegex(ValueError, "does not appear to be an IPv4 or IPv6 address"):
            validate_records("example.com", [
                {"name": "www", "type": "A", "ttl": 60, "value": "not-an-ip"},
            ])

        with self.assertRaisesRegex(ValueError, "CNAME cannot coexist"):
            validate_records("example.com", [
                {"name": "alias", "type": "CNAME", "ttl": 60, "value": "target.example.com"},
                {"name": "alias", "type": "TXT", "ttl": 60, "value": "owned"},
            ])

    def test_load_zone_and_import_validates_before_provider_write(self):
        document = {
            "zone": "example.com",
            "records": [{"name": "www", "type": "A", "ttl": 300, "value": "192.0.2.10"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zone.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            provider = FakeProvider()

            self.assertEqual(len(load_zone(path)[1]), 1)
            self.assertEqual(import_zone(path, provider), 1)
            self.assertEqual(provider.calls[0][0], "example.com")

            document["records"][0]["value"] = "invalid"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                import_zone(path, provider)
            self.assertEqual(len(provider.calls), 1)

    def test_snapshot_can_be_restored(self):
        records = [{"name": "www", "type": "A", "ttl": 300, "value": "192.0.2.10"}]
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshots" / "before.json"
            save_snapshot(snapshot_path, "example.com", records)
            provider = FakeProvider()

            self.assertEqual(rollback(snapshot_path, provider), 1)
            self.assertEqual(provider.calls[0][1][0]["name"], "www.example.com")

    def test_propagation_waits_until_expected_answer_is_visible(self):
        calls = {"resolver-a": 0}
        waits = []

        def resolver(name, record_type, label):
            calls[label] += 1
            return [] if calls[label] == 1 else ["192.0.2.10"]

        result = check_propagation(
            [{"name": "www.example.com", "type": "A", "value": "192.0.2.10"}],
            {"resolver-a": resolver},
            attempts=2,
            interval=1,
            wait=waits.append,
        )

        self.assertTrue(result.converged)
        self.assertEqual(waits, [1])

    def test_propagation_reports_missing_answers(self):
        result = check_propagation(
            [{"name": "www.example.com", "type": "A", "value": "192.0.2.10"}],
            {"resolver-a": lambda *_: []},
        )

        self.assertFalse(result.converged)
        self.assertIn("192.0.2.10", result.missing["resolver-a"][0])


if __name__ == "__main__":
    unittest.main()