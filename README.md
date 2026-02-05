# work_management_tools
Various scripts and tools used to help in the management aspect of work life.   For example, scripts that fetch data from JIRA

## JIRA time entries export
`jira_time_entries_export.py` exports worklog entries for a specific user across
a list of JIRA issues. The script is read-only and uses the JIRA REST API.

### Configuration
Provide credentials via environment variables or an INI config file.

Environment variables:
- `JIRA_BASE_URL` (e.g. `https://your-domain.atlassian.net`)
- `JIRA_EMAIL` (account email or username)
- `JIRA_API_TOKEN` (API token or password)
- `JIRA_WORKLOG_USER` (accountId, name, displayName, or email)
- `JIRA_API_VERSION` (optional, defaults to `3`)

Config file (see `jira_config.example.ini`):
```
[jira]
base_url = https://your-domain.atlassian.net
email = you@example.com
api_token = your_api_token
worklog_user = your_account_id_or_email
api_version = 3
```

### Usage
```
python3 jira_time_entries_export.py \
  --issues PROJ-1,PROJ-2 \
  --config jira_config.ini \
  --output jira_time_entries.csv
```

Filter by a date range (inclusive):
```
python3 jira_time_entries_export.py \
  --issues PROJ-1,PROJ-2 \
  --startdate 2024-01-01 \
  --enddate 2024-01-31 \
  --output jira_time_entries.csv
```

Override the worklog user on the command line:
```
python3 jira_time_entries_export.py \
  --issues PROJ-1,PROJ-2 \
  --user 5d1234567890abcdef123456 \
  --output jira_time_entries.csv
```

Output columns:
- JIRA Identifier (Issue Key)
- Time Spent (seconds)
- Time Spent In Hours
- UserName
- Worklog Date (YYYY-MM-DD)
