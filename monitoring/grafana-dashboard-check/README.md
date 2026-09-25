# Grafana dashboard check

Check dashboard panel layouts offline and run visible PromQL expressions against Prometheus to find errors and unexpectedly empty results.

## Contents

- [Use case](#use-case)
- [Prerequisites](#prerequisites)
- [Guided setup](#guided-setup)
- [Manual setup](#manual-setup)
- [Inputs](#inputs)
- [Permissions](#permissions)
- [Dry run](#dry-run)
- [Changes made](#changes-made)
- [Safeguard reasoning](#safeguard-reasoning)
- [Rollback](#rollback)
- [Troubleshooting](#troubleshooting)
- [Exit behavior](#exit-behavior)

## Use case

Run `layout` after generating or editing dashboard JSON. It finds overlapping panels, invalid sizes, missing grid coordinates, panels outside the 24-column grid, negative row positions, duplicate panel IDs, duplicate dashboard UIDs, and mixed datasource UIDs within a dashboard. Collapsed rows have separate grid spaces.

Run `queries` before publishing dashboards. It reads every visible target expression, including targets inside collapsed rows, and sends instant queries to Prometheus. A result must contain data unless its exact panel title is allowed empty. Hidden targets are skipped. A successful query does not prove that the metric or visualization means what you intended.

## Prerequisites

- Platform: a Python command-line environment; minimum Python 3.11. No external runtime packages are required. Operating-system compatibility is pending.
- Local dashboard JSON files with classic `panels` and `gridPos` fields. Grafana API responses wrapped in a `dashboard` object are also accepted.
- Network access to Prometheus for `queries`, except with `--dry-run`.
- Read access to a custom CA file if configured, and credentials in named environment variables if authentication is enabled.

Tested status: **Locally checked; live matrix pending**.

## Guided setup

Use an ordinary user account. The configurator reads local dashboards and creates only a local JSON config. Both checks are read-only. `layout` and `queries --dry-run` work offline. No runtime rollback is needed; remove the local config to undo setup. Exit codes are listed under [Exit behavior](#exit-behavior).

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/grafana-dashboard-check"
DASHBOARD_DIR="$HOME/grafana/dashboards"
python "$TOOL_DIR/configure.py" --dashboard "$DASHBOARD_DIR"
```

This writes ignored `config.local.json` beside the tool and refuses to replace an existing file. There is no overwrite option. Use `--output PATH` to create another file, or review and remove the old local config first.

The configurator seeds regex variables with `.*` and uses the current literal value for interval and constant variables. Datasource and ad hoc variables are skipped. Review every `CUSTOMIZE:` value, especially variables used with exact-match operators, where `.*` is a literal string. No server is contacted. Without `--dashboard`, setup leaves placeholder paths and variables for you to edit.

## Manual setup

Bash, from a folder where `config.local.json` does not already exist:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/grafana-dashboard-check"
CONFIG_PATH="$TOOL_DIR/config.local.json"
cp -n "$TOOL_DIR/config.example.json" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
python "$TOOL_DIR/grafana_dashboards.py" layout --config "$CONFIG_PATH"
python "$TOOL_DIR/grafana_dashboards.py" queries --config "$CONFIG_PATH" --dry-run
python "$TOOL_DIR/grafana_dashboards.py" queries --config "$CONFIG_PATH"
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/monitoring/grafana-dashboard-check'
$ConfigPath = Join-Path $ToolDir 'config.local.json'
if (Test-Path $ConfigPath) { throw 'Local config already exists' }
Copy-Item (Join-Path $ToolDir 'config.example.json') $ConfigPath
notepad $ConfigPath
py (Join-Path $ToolDir 'grafana_dashboards.py') layout --config $ConfigPath
py (Join-Path $ToolDir 'grafana_dashboards.py') queries --config $ConfigPath --dry-run
py (Join-Path $ToolDir 'grafana_dashboards.py') queries --config $ConfigPath
```

Replace every `CUSTOMIZE:` value before querying. Keep config and allow-empty JSON files outside dashboard directories: directory inputs recursively include every `*.json` file.

## Inputs

Pass local configuration explicitly with `--config`. It is never loaded automatically. Command-line values override local config, which overrides documented defaults. Relative config paths start at the config's directory; command-line paths start at the current directory. Repeated file inputs are checked once.

| Setting | Default and override |
|---|---|
| `dashboards.paths` | No default; positional file or directory paths replace this list |
| `dashboards.require_single_datasource` | `true`; `layout --allow-mixed-datasources` or `--require-single-datasource` overrides it |
| `prometheus.url` | `http://127.0.0.1:9090`; override with `--url`, without `/api/v1`; a path prefix is accepted |
| `prometheus.timeout_seconds` | `30` per request; override with `--timeout` |
| `prometheus.ca_file` | Empty, using system trust; `--ca-file` supplies a PEM path and `--no-ca-file` clears it |
| Authentication | None; `--bearer-token-env`, or `--basic-username` with `--basic-password-env`; `--no-auth` clears configured authentication |
| `queries.variables` | Built-ins below; repeat `--var NAME=VALUE` to override individual names |
| `queries.allow_empty_titles` | Empty list; repeated `--allow-empty TITLE` replaces this list |
| `queries.allow_empty_file` | Empty; `--allow-empty-file PATH` selects a JSON list of exact titles, combined with the title list |

Built-ins are `__interval=1m`, `__interval_ms=60000`, `__rate_interval=5m`, `__range=6h`, `__range_ms=21600000`, and `__range_s=21600`. These represent a six-hour range with a one-minute step. Both `$name` and `${name}` are substituted by whole name. Other Grafana formatting syntax is not supported. Unknown variables stay unchanged; inspect the dry run and provide missing substitutions.

An allow-empty file contains a JSON array such as `["Recent restarts"]`. Titles match exactly across all dashboards, so choose them carefully. Missing or invalid allow-empty files fail the check. No title is implicitly allowed by default.

Credential fields hold environment-variable names, never tokens or passwords. Set `prometheus.bearer_token_env` to `PROMETHEUS_TOKEN`, then use a hidden prompt:

```bash
read -r -s -p 'Prometheus token: ' PROMETHEUS_TOKEN
export PROMETHEUS_TOKEN
python "$TOOL_DIR/grafana_dashboards.py" queries --config "$CONFIG_PATH"
unset PROMETHEUS_TOKEN
```

For basic authentication, set `basic_username` and `basic_password_env` and leave `bearer_token_env` empty. Use HTTPS when sending credentials beyond loopback. Authentication modes cannot be combined in the config; a command-line authentication mode replaces the configured mode.

## Permissions

No elevation is required. The checks need read access to dashboards, config, and any CA or allow-empty file. The query credential needs only permission to read the Prometheus query API. The configurator needs write access to its output directory.

## Dry run

`layout` is always offline. Preview the interpolated queries without contacting Prometheus or reading credentials:

```bash
python "$TOOL_DIR/grafana_dashboards.py" queries --config "$CONFIG_PATH" --dry-run
```

The preview prints expressions and allowed-empty markers. It cannot prove that a query parses in Prometheus or returns data. To check the included layout fixtures:

```bash
python "$TOOL_DIR/grafana_dashboards.py" layout "$TOOL_DIR/tests/fixtures/valid"
```

## Changes made

Both subcommands leave dashboard files and remote state unchanged. `queries` sends only GET requests to `/api/v1/query`. The configurator creates its requested output file and any missing parent directories. It never edits dashboards or existing config files.

## Safeguard reasoning

Layout checks catch panel placement that Grafana might silently rearrange. Query errors always fail, even for allowed-empty panels. Dashboards without queries fail the query check. Missing input files and malformed JSON fail before requests start.

Redirects are rejected so authorization headers cannot be forwarded to another endpoint. Endpoint failures such as authentication errors stop remaining queries. Credential values, including encoded basic-auth headers, are redacted from server error messages. Credentials in URL fields are rejected. Config creation uses exclusive file creation to prevent a concurrent run from replacing a local config.

## Rollback

There is no remote change to undo. Remove a generated local config and any empty parent directories if setup is no longer needed. The tracked example and dashboards remain unchanged. This tool does not publish or restore dashboards.

## Troubleshooting

- `layout-problem`: adjust panel coordinates, dimensions, IDs, or datasource UIDs in the dashboard source, then rerun.
- `query-empty`: check the substituted label selectors and metric availability. Allow the title only when empty data is correct.
- `query-error`: inspect the PromQL and dry-run substitutions. Allow-empty does not suppress query errors.
- `environment variable ... is empty or unset`: set the named variable in the terminal running the command.
- `Prometheus returned HTTP 3xx`: use the final base URL; redirects are rejected.
- `no PromQL queries found`: select dashboard files containing visible PromQL targets.
- `cannot read allow-empty file`: correct the configured path; no fallback list is used.

## Exit behavior

- `0`: layout passed, every query returned data or was allowed empty, or offline preview completed. Configurator success also returns `0`.
- `1`: invalid config or unreadable input, missing credentials, TLS or connection failure, rejected redirect, or HTTP `401`, `403`, `404`, or `407`. Configurator input errors and refusal to overwrite also return `1`.
- `2`: layout findings, query errors, dashboards without queries, or command-line usage errors. Query errors take precedence over empty results.
- `3`: one or more queries returned unexpectedly empty results without query errors.
