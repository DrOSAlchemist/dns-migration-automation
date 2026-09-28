import os
import unittest
from unittest.mock import patch

from src.config import _expand


class ConfigExpansionTests(unittest.TestCase):
	def test_expands_set_variables_and_preserves_optional_missing_variables(self):
		with patch.dict(os.environ, {"DNS_HOST": "dns.example.net"}, clear=False):
			self.assertEqual(
				_expand({"base_url": "https://${DNS_HOST}", "token": "${OPTIONAL_TOKEN}"}),
				{"base_url": "https://dns.example.net", "token": "${OPTIONAL_TOKEN}"},
			)

	def test_expands_default_for_missing_variable(self):
		with patch.dict(os.environ, {}, clear=True):
			self.assertEqual(_expand("${MISSING:default}"), "default")


if __name__ == "__main__":
	unittest.main()