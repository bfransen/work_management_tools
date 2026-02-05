#!/usr/bin/env python3
"""
Export JIRA worklog time entries for a specific user.

Reads credentials from environment variables or an optional config file.
Writes a CSV with one row per matching worklog entry.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    worklog_user: str
    api_version: str = "3"


class JiraApiError(RuntimeError):
    def __init__(self, status_code: int, url: str, message: str) -> None:
        super().__init__(f"HTTP {status_code} while calling {url}: {message}")
        self.status_code = status_code
        self.url = url
        self.message = message


def _read_config_file(path: str) -> Dict[str, str]:
    parser = configparser.ConfigParser()
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(f"Config file not found: {path}")

    if "jira" not in parser:
        raise ValueError("Config file missing [jira] section")

    jira_section = parser["jira"]
    return {key: jira_section.get(key, "").strip() for key in jira_section.keys()}


def _env_or_config(
    env_name: str,
    config_key: str,
    config: Dict[str, str],
    default: Optional[str] = None,
) -> Optional[str]:
    value = os.getenv(env_name)
    if value is not None and value.strip():
        return value.strip()
    if config_key in config and config[config_key]:
        return config[config_key]
    return default


def load_jira_config(config_path: Optional[str]) -> JiraConfig:
    config_values: Dict[str, str] = {}
    if config_path:
        config_values = _read_config_file(config_path)

    base_url = _env_or_config("JIRA_BASE_URL", "base_url", config_values)
    email = _env_or_config("JIRA_EMAIL", "email", config_values)
    api_token = _env_or_config("JIRA_API_TOKEN", "api_token", config_values)
    api_version = _env_or_config("JIRA_API_VERSION", "api_version", config_values, "3")

    if not base_url:
        raise ValueError("Missing JIRA_BASE_URL or base_url in config")
    if not email:
        raise ValueError("Missing JIRA_EMAIL or email in config")
    if not api_token:
        raise ValueError("Missing JIRA_API_TOKEN or api_token in config")

    worklog_user = _env_or_config(
        "JIRA_WORKLOG_USER",
        "worklog_user",
        config_values,
        default=email,
    )
    if not worklog_user:
        raise ValueError("Missing JIRA_WORKLOG_USER or worklog_user in config")

    return JiraConfig(
        base_url=base_url.rstrip("/"),
        email=email,
        api_token=api_token,
        worklog_user=worklog_user,
        api_version=api_version,
    )


def _build_auth_header(email: str, api_token: str) -> str:
    auth_bytes = f"{email}:{api_token}".encode("utf-8")
    return base64.b64encode(auth_bytes).decode("ascii")


def _jira_get_json(base_url: str, auth_header: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{base_url}{path}?{query}" if query else f"{base_url}{path}"
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Basic {auth_header}")
    request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
        raise JiraApiError(exc.code, url, body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error while calling {url}: {exc}") from exc


def _iter_worklogs(
    base_url: str,
    auth_header: str,
    api_version: str,
    issue_key: str,
) -> Iterable[Dict[str, Any]]:
    start_at = 0
    max_results = 100

    while True:
        path = f"/rest/api/{api_version}/issue/{issue_key}/worklog"
        data = _jira_get_json(
            base_url,
            auth_header,
            path,
            {"startAt": start_at, "maxResults": max_results},
        )
        worklogs = data.get("worklogs", [])
        for entry in worklogs:
            yield entry

        total = data.get("total", 0)
        start_at += len(worklogs)
        if start_at >= total or not worklogs:
            break


def _matches_user(worklog: Dict[str, Any], user_identifier: str) -> bool:
    author = worklog.get("author") or {}
    candidates = [
        author.get("accountId"),
        author.get("name"),
        author.get("displayName"),
        author.get("emailAddress"),
    ]
    return any(candidate == user_identifier for candidate in candidates if candidate)


def _extract_author_name(worklog: Dict[str, Any]) -> str:
    author = worklog.get("author") or {}
    return author.get("displayName") or author.get("name") or author.get("accountId") or ""


def _parse_date_arg(raw_value: Optional[str], label: str) -> Optional[date]:
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be in YYYY-MM-DD format.") from exc


def _parse_worklog_started_date(worklog: Dict[str, Any]) -> Optional[date]:
    started = worklog.get("started")
    if not started:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(started, fmt).date()
        except ValueError:
            continue
    return None


def _within_date_range(
    started_date: Optional[date],
    start_date: Optional[date],
    end_date: Optional[date],
) -> bool:
    if not start_date and not end_date:
        return True
    if started_date is None:
        return False
    if start_date and started_date < start_date:
        return False
    if end_date and started_date > end_date:
        return False
    return True


def fetch_time_entries(
    config: JiraConfig,
    issue_keys: Iterable[str],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[Tuple[str, int, float, str, Optional[date]]]:
    auth_header = _build_auth_header(config.email, config.api_token)
    entries: List[Tuple[str, int, float, str, Optional[date]]] = []

    for issue_key in issue_keys:
        try:
            for worklog in _iter_worklogs(
                config.base_url, auth_header, config.api_version, issue_key
            ):
                if not _matches_user(worklog, config.worklog_user):
                    continue
                started_date = _parse_worklog_started_date(worklog)
                if not _within_date_range(started_date, start_date, end_date):
                    continue
                time_spent_seconds = int(worklog.get("timeSpentSeconds", 0))
                hours = round(time_spent_seconds / 3600, 2)
                author_name = _extract_author_name(worklog)
                entries.append(
                    (issue_key, time_spent_seconds, hours, author_name, started_date)
                )
        except JiraApiError as exc:
            if exc.status_code == 404:
                print(
                    f"Warning: issue {issue_key} not found (HTTP 404). Skipping.",
                    file=sys.stderr,
                )
                continue
            raise

    return entries


def _parse_issue_keys(raw_issues: str) -> List[str]:
    return [key.strip() for key in raw_issues.split(",") if key.strip()]


def write_csv(
    output_path: str, entries: Iterable[Tuple[str, int, float, str, Optional[date]]]
) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(
            [
                "JIRA Identifier (Issue Key)",
                "Time Spent",
                "Time Spent In Hours",
                "UserName",
                "Worklog Date",
            ]
        )
        for issue_key, time_spent_seconds, hours, author_name, worklog_date in entries:
            writer.writerow(
                [
                    issue_key,
                    time_spent_seconds,
                    hours,
                    author_name,
                    worklog_date.isoformat() if worklog_date else "",
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export JIRA worklog entries for a specific user."
    )
    parser.add_argument(
        "--issues",
        "-i",
        required=True,
        help="Comma-separated list of JIRA issue keys (e.g. PROJ-1,PROJ-2).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="jira_time_entries.csv",
        help="Output CSV path (default: jira_time_entries.csv).",
    )
    parser.add_argument(
        "--config",
        "-c",
        help="Path to INI config file containing JIRA credentials.",
    )
    parser.add_argument(
        "--user",
        "-u",
        help=(
            "Worklog user identifier override (accountId, name, displayName, "
            "or email). Defaults to JIRA_WORKLOG_USER or the auth email."
        ),
    )
    parser.add_argument(
        "--startdate",
        help="Start date (YYYY-MM-DD). Only include worklogs on/after this date.",
    )
    parser.add_argument(
        "--enddate",
        help="End date (YYYY-MM-DD). Only include worklogs on/before this date.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_jira_config(args.config)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.user:
        config = JiraConfig(
            base_url=config.base_url,
            email=config.email,
            api_token=config.api_token,
            worklog_user=args.user,
            api_version=config.api_version,
        )

    try:
        start_date = _parse_date_arg(args.startdate, "startdate")
        end_date = _parse_date_arg(args.enddate, "enddate")
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if start_date and end_date and start_date > end_date:
        print("Configuration error: startdate must be on/before enddate.", file=sys.stderr)
        return 2

    issue_keys = _parse_issue_keys(args.issues)
    if not issue_keys:
        print("No valid issue keys provided.", file=sys.stderr)
        return 2

    try:
        entries = fetch_time_entries(config, issue_keys, start_date=start_date, end_date=end_date)
    except RuntimeError as exc:
        print(f"Failed to fetch worklogs: {exc}", file=sys.stderr)
        return 1

    write_csv(args.output, entries)
    print(f"Wrote {len(entries)} worklog entries to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
