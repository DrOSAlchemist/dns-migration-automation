from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src import cli


class FakeProvider:
	def __init__(self, records, fail_first_apply=False):
		self.records = records
		self.calls = []
		self.rollback_calls = []
		self.fail_first_apply = fail_first_apply

	def list_records(self, _zone):
		return self.records

	def apply_diff(self, _zone, changes):
		self.calls.append(changes)
		if self.fail_first_apply and len(self.calls) == 1:
			raise RuntimeError("simulated write failure")

	def replace_zone(self, _zone, records):
		self.rollback_calls.append(records)
		self.records = records


class CLITests(unittest.TestCase):
	def _zone_file(self, directory):
		path = Path(directory) / "zone.json"
		path.write_text(json.dumps({
			"zone": "example.com",
			"records": [{"name": "www", "type": "A", "ttl": 60, "value": "192.0.2.2"}],
		}), encoding="utf-8")
		return path

	def test_validate_command_reports_valid_zone(self):
		with tempfile.TemporaryDirectory() as directory:
			zone_file = self._zone_file(directory)
			output = io.StringIO()
			with redirect_stdout(output):
				result = cli.main(["validate", str(zone_file)])

		self.assertEqual(result, 0)
		self.assertIn('"valid": true', output.getvalue())

	def test_apply_requires_confirmation_before_provider_creation(self):
		with tempfile.TemporaryDirectory() as directory:
			zone_file = self._zone_file(directory)
			stderr = io.StringIO()
			with patch("src.cli.build_provider") as build_provider, redirect_stderr(stderr):
				result = cli.main([
					"apply", "--config", "unused.yaml", "--environment", "test", str(zone_file),
				])

		self.assertEqual(result, 2)
		self.assertIn("requires --confirm", stderr.getvalue())
		build_provider.assert_not_called()

	def test_apply_saves_snapshot_and_attempts_automatic_rollback(self):
		before = [{"name": "www.example.com", "type": "A", "ttl": 300, "value": "192.0.2.1"}]
		provider = FakeProvider(before, fail_first_apply=True)
		with tempfile.TemporaryDirectory() as directory:
			zone_file = self._zone_file(directory)
			snapshot_dir = Path(directory) / "snapshots"
			output = io.StringIO()
			error_output = io.StringIO()
			with patch("src.cli._environment", return_value={"zone": "example.com"}), \
				patch("src.cli.build_provider", return_value=provider), \
				redirect_stdout(output), redirect_stderr(error_output):
				result = cli.main([
					"apply", "--environment", "test", "--confirm", "--snapshot-dir", str(snapshot_dir),
					str(zone_file),
				])

			files = list(snapshot_dir.glob("*.json"))
			self.assertEqual(len(files), 1)
			self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["records"], before)
			self.assertEqual(files[0].name.split("-")[0], "test")

		self.assertEqual(result, 2)
		self.assertEqual(len(provider.calls), 1)
		self.assertEqual(len(provider.rollback_calls), 1)
		self.assertEqual(provider.records, before)
		self.assertIn("automatic rollback", error_output.getvalue())

	def test_ipam_lookup_reads_inventory_file(self):
		with tempfile.TemporaryDirectory() as directory:
			inventory = Path(directory) / "networks.json"
			inventory.write_text(json.dumps([{
				"network": "10.1.0.0/16",
				"metadata": {"site": "west"},
			}]), encoding="utf-8")
			output = io.StringIO()
			with redirect_stdout(output):
				result = cli.main(["ipam-lookup", "10.1.2.3", "--inventory", str(inventory)])

		self.assertEqual(result, 0)
		self.assertIn('"network": "10.1.0.0/16"', output.getvalue())
		self.assertIn('"site": "west"', output.getvalue())


if __name__ == "__main__":
	unittest.main()