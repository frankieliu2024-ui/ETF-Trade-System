import tempfile
import unittest
from pathlib import Path

from scripts.state_manager import read_json_checked


class EmptyJsonReadContractTests(unittest.TestCase):
    def test_zero_byte_json_is_unverified_not_verified_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large-state.json"
            path.write_bytes(b"")
            value, status = read_json_checked(path, {"fallback": True})
        self.assertEqual(value, {"fallback": True})
        self.assertEqual(status, "READ_UNVERIFIED")

    def test_valid_json_remains_read_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"notifications": []}', encoding="utf-8")
            value, status = read_json_checked(path, {})
        self.assertEqual(value, {"notifications": []})
        self.assertEqual(status, "READ_OK")


if __name__ == "__main__":
    unittest.main()
