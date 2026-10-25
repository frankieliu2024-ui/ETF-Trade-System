import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from scripts import scheduled_review_completion_recovery as recovery


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _remote(content, sha="blob"):
    return {"content": base64.b64encode(content).decode("ascii"), "sha": sha}


class ScheduledReviewWriteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "completion.json"
        self.target.write_bytes(b'{"request_id":"r1","content_hash":"h1"}')
        self.env = patch.dict(
            recovery.os.environ,
            {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "owner/repo",
            },
            clear=False,
        )
        self.env.start()
        self.root = patch.object(recovery, "ROOT", Path(self.tmp.name))
        self.root.start()

    def tearDown(self):
        self.root.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_transient_write_failure_retries_and_reads_back(self):
        calls = []
        def fake_open(req, timeout=30):
            calls.append((req.get_method(), req.full_url))
            if req.get_method() == "GET":
                if sum(method == "GET" for method, _ in calls) in (1, 3):
                    raise HTTPError(req.full_url, 404, "missing", {}, None)
                return _Response(_remote(self.target.read_bytes()))
            if len([method for method, _ in calls if method == "PUT"]) == 1:
                raise TimeoutError("transient timeout")
            return _Response({}, 201)
        with patch.object(recovery.urllib_request, "urlopen", side_effect=fake_open):
            relative, receipt = recovery.persist_completion_with_recovery(self.target)
        self.assertEqual(relative, "completion.json")
        self.assertTrue(receipt["read_back"])
        self.assertEqual(receipt["attempts"], 2)

    def test_identical_existing_completion_is_idempotent_success(self):
        with patch.object(
            recovery.urllib_request,
            "urlopen",
            return_value=_Response(_remote(self.target.read_bytes())),
        ) as mocked:
            _, receipt = recovery.persist_completion_with_recovery(self.target)
        self.assertTrue(receipt["already_present"])
        self.assertEqual(mocked.call_count, 1)

    def test_conflicting_existing_completion_fails_closed(self):
        with patch.object(
            recovery.urllib_request,
            "urlopen",
            return_value=_Response(_remote(b'{"request_id":"other"}')),
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery exhausted"):
                recovery.persist_completion_with_recovery(self.target)


if __name__ == "__main__":
    unittest.main()
