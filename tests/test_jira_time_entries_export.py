import json
from datetime import date
from unittest import mock

import pytest

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


def test_jira_get_json_parses_payload_and_sets_headers(monkeypatch) -> None:
    payload = {"worklogs": [], "total": 0}

    def fake_urlopen(request, timeout=30):
        assert timeout == 30
        headers = dict(request.header_items())
        assert headers.get("Authorization") == "Basic abc123"
        assert headers.get("Accept") == "application/json"
        assert request.full_url.startswith(
            "https://example.atlassian.net/rest/api/3/issue/PROJ-1/worklog"
        )
        assert "startAt=0" in request.full_url
        return FakeResponse(json.dumps(payload))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = jtee._jira_get_json(
        "https://example.atlassian.net",
        "abc123",
        "/rest/api/3/issue/PROJ-1/worklog",
        {"startAt": 0},
    )

    assert result == payload


def test_iter_worklogs_paginates(monkeypatch) -> None:
    responses = [
        {"worklogs": [{"id": 1}, {"id": 2}], "total": 3},
        {"worklogs": [{"id": 3}], "total": 3},
    ]

    def fake_get_json(base_url, auth_header, path, params):
        return responses.pop(0)

    get_json_mock = mock.Mock(side_effect=fake_get_json)
    monkeypatch.setattr("jira_time_entries_export._jira_get_json", get_json_mock)

    worklogs = list(
        jtee._iter_worklogs("https://example.atlassian.net", "auth", "3", "PROJ-1")
    )

    assert [worklog["id"] for worklog in worklogs] == [1, 2, 3]
    assert get_json_mock.call_count == 2


def test_fetch_time_entries_filters_by_user(monkeypatch) -> None:
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

    monkeypatch.setattr("jira_time_entries_export._iter_worklogs", fake_iter)
    entries = jtee.fetch_time_entries(config, ["PROJ-1", "PROJ-2"])

    assert entries == [
        ("PROJ-1", 3600, 1.0, "Alice"),
        ("PROJ-2", 0, 0.0, "Alice"),
    ]


def test_fetch_time_entries_filters_by_date_range(monkeypatch) -> None:
    config = jtee.JiraConfig(
        base_url="https://example.atlassian.net",
        email="user@example.com",
        api_token="token",
        worklog_user="abc123",
        api_version="3",
    )
    worklogs = [
        {
            "author": {"accountId": "abc123", "displayName": "Alice"},
            "timeSpentSeconds": 3600,
            "started": "2024-01-01T08:00:00.000+0000",
        },
        {
            "author": {"accountId": "abc123", "displayName": "Alice"},
            "timeSpentSeconds": 1800,
            "started": "2024-01-02T08:00:00.000+0000",
        },
        {
            "author": {"accountId": "abc123", "displayName": "Alice"},
            "timeSpentSeconds": 1200,
            "started": "2024-01-03T08:00:00+0000",
        },
        {
            "author": {"accountId": "abc123", "displayName": "Alice"},
            "timeSpentSeconds": 600,
            "started": "2024-01-04T08:00:00.000+0000",
        },
    ]

    def fake_iter(base_url, auth_header, api_version, issue_key):
        return worklogs

    monkeypatch.setattr("jira_time_entries_export._iter_worklogs", fake_iter)
    entries = jtee.fetch_time_entries(
        config,
        ["PROJ-1"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 3),
    )

    assert entries == [
        ("PROJ-1", 1800, 0.5, "Alice"),
        ("PROJ-1", 1200, 0.33, "Alice"),
    ]


def test_write_csv_outputs_header_and_rows(tmp_path) -> None:
    entries = [
        ("PROJ-1", 3600, 1.0, "Alice"),
        ("PROJ-2", 1800, 0.5, "Bob"),
    ]
    path = tmp_path / "out.csv"
    jtee.write_csv(str(path), entries)

    rows = path.read_text(encoding="utf-8").splitlines()
    assert (
        rows[0]
        == "JIRA Identifier (Issue Key),Time Spent,Time Spent In Hours,UserName"
    )
    assert rows[1] == "PROJ-1,3600,1.0,Alice"
    assert rows[2] == "PROJ-2,1800,0.5,Bob"


def test_load_jira_config_uses_config_file(monkeypatch, tmp_path) -> None:
    config_contents = (
        "[jira]\n"
        "base_url = https://example.atlassian.net/\n"
        "email = config@example.com\n"
        "api_token = token123\n"
        "worklog_user = abc123\n"
        "api_version = 2\n"
    )
    config_path = tmp_path / "jira.ini"
    config_path.write_text(config_contents, encoding="utf-8")

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.delenv("JIRA_WORKLOG_USER", raising=False)
    monkeypatch.delenv("JIRA_API_VERSION", raising=False)

    config = jtee.load_jira_config(str(config_path))

    assert config.base_url == "https://example.atlassian.net"
    assert config.email == "config@example.com"
    assert config.api_token == "token123"
    assert config.worklog_user == "abc123"
    assert config.api_version == "2"
