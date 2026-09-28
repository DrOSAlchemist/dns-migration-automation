import unittest

from src.zone_diff import diff_zone, group_records


class ZoneDiffTests(unittest.TestCase):
	def test_diff_classifies_add_remove_and_replace(self):
		current = [
			{"name": "old.example.com", "type": "A", "ttl": 300, "value": "192.0.2.1"},
			{"name": "www.example.com", "type": "A", "ttl": 300, "value": "192.0.2.2"},
		]
		desired = [
			{"name": "new.example.com", "type": "TXT", "ttl": 60, "value": "ready"},
			{"name": "www.example.com", "type": "A", "ttl": 60, "value": "192.0.2.3"},
		]

		diff = diff_zone(current, desired)

		self.assertEqual([item.name for item in diff.added], ["new.example.com"])
		self.assertEqual([item.name for item in diff.removed], ["old.example.com"])
		self.assertEqual(diff.changed[0][0].ttl, 300)
		self.assertEqual(diff.changed[0][1].ttl, 60)

	def test_diff_is_empty_for_same_records_in_different_order(self):
		records = [
			{"name": "example.com", "type": "TXT", "ttl": 60, "value": "b"},
			{"name": "example.com", "type": "TXT", "ttl": 60, "value": "a"},
		]

		self.assertTrue(diff_zone(records, list(reversed(records))).is_empty)

	def test_group_records_rejects_mixed_ttls(self):
		with self.assertRaisesRegex(ValueError, "multiple TTL"):
			group_records([
				{"name": "www.example.com", "type": "A", "ttl": 60, "value": "192.0.2.1"},
				{"name": "www.example.com", "type": "A", "ttl": 300, "value": "192.0.2.2"},
			])


if __name__ == "__main__":
	unittest.main()