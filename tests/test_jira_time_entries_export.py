import json
import os
import tempfile
import unittest
from unittest import mock

import jira_time_entries_export as jtee


class FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


class JiraTimeEntriesExportTests(unittest.TestCase):
    def test_jira_get_json_parses_payload_and_sets_headers(self) -> None:
        payload = {"worklogs": [], "total": 0}

        def fake_urlopen(request, timeout=30):
            self.assertEqual(timeout, 30)
            headers = dict(request.header_items())
            self.assertEqual(headers.get("Authorization"), "Basic abc123")
            self.assertEqual(headers.get("Accept"), "application/json")
            self.assertTrue(
                request.full_url.startswith(
                    "https://example.atlassian.net/rest/api/3/issue/PROJ-1/worklog"
                )
            )
            self.assertIn("startAt=0", request.full_url)
            return FakeResponse(json.dumps(payload))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = jtee._jira_get_json(
                "https://example.atlassian.net",
                "abc123",
                "/rest/api/3/issue/PROJ-1/worklog",
                {"startAt": 0},
            )

        self.assertEqual(result, payload)

    def test_iter_worklogs_paginates(self) -> None:
        responses = [
            {"worklogs": [{"id": 1}, {"id": 2}], "total": 3},
            {"worklogs": [{"id": 3}], "total": 3},
        ]

        def fake_get_json(base_url, auth_header, path, params):
            return responses.pop(0)

        with mock.patch(
            "jira_time_entries_export._jira_get_json", side_effect=fake_get_json
        ) as mock_get_json:
            worklogs = list(
                jtee._iter_worklogs(
                    "https://example.atlassian.net", "auth", "3", "PROJ-1"
                )
            )

        self.assertEqual([worklog["id"] for worklog in worklogs], [1, 2, 3])
        self.assertEqual(mock_get_json.call_count, 2)

    def test_fetch_time_entries_filters_by_user(self) -> None:
        config = jtee.JiraConfig(
            base_url="https://example.atlassian.net",
            email="user@example.com",
            api_token="token",
            worklog_user="abc123",
            api_version="3",
        )
        worklogs_proj1 = [
            {
                "author": {"accountId": "abc123", "displayName": "Alice"},
                "timeSpentSeconds": 3600,
            },
            {
                "author": {"accountId": "def456", "displayName": "Bob"},
                "timeSpentSeconds": 1800,
            },
        ]
        worklogs_proj2 = [
            {
                "author": {"accountId": "abc123", "displayName": "Alice"},
                "timeSpentSeconds": 0,
            }
        ]

        def fake_iter(base_url, auth_header, api_version, issue_key):
            return worklogs_proj1 if issue_key == "PROJ-1" else worklogs_proj2

        with mock.patch("jira_time_entries_export._iter_worklogs", side_effect=fake_iter):
            entries = jtee.fetch_time_entries(config, ["PROJ-1", "PROJ-2"])

        self.assertEqual(
            entries,
            [
                ("PROJ-1", 3600, 1.0, "Alice"),
                ("PROJ-2", 0, 0.0, "Alice"),
            ],
        )

    def test_write_csv_outputs_header_and_rows(self) -> None:
        entries = [
            ("PROJ-1", 3600, 1.0, "Alice"),
            ("PROJ-2", 1800, 0.5, "Bob"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.csv")
            jtee.write_csv(path, entries)
            with open(path, "r", encoding="utf-8") as handle:
                rows = [line.strip() for line in handle.readlines()]

        self.assertEqual(
            rows[0],
            "JIRA Identifier (Issue Key),Time Spent,Time Spent In Hours,UserName",
        )
        self.assertEqual(rows[1], "PROJ-1,3600,1.0,Alice")
        self.assertEqual(rows[2], "PROJ-2,1800,0.5,Bob")

    def test_load_jira_config_uses_config_file(self) -> None:
        config_contents = (
            "[jira]\n"
            "base_url = https://example.atlassian.net/\n"
            "email = config@example.com\n"
            "api_token = token123\n"
            "worklog_user = abc123\n"
            "api_version = 2\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(config_contents)
            config_path = handle.name

        try:
            with mock.patch.dict(os.environ, {}, clear=True):
                config = jtee.load_jira_config(config_path)

            self.assertEqual(config.base_url, "https://example.atlassian.net")
            self.assertEqual(config.email, "config@example.com")
            self.assertEqual(config.api_token, "token123")
            self.assertEqual(config.worklog_user, "abc123")
            self.assertEqual(config.api_version, "2")
        finally:
            os.unlink(config_path)


if __name__ == "__main__":
    unittest.main()
