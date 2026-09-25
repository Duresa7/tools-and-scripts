# UniFi flow dashboards

Generate Splunk Dashboard Studio definitions and verify queries against the Splunk REST API.

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

Build and validate Splunk Dashboard Studio definitions and Splunk application configurations for UniFi traffic flows and syslog events. Hand-crafting large JSON layouts causes unbalanced structures and mismatched color palettes. This tool generates consistent Dashboard Studio views, search macros, field extractions, and correlation searches from centralized design tokens. The verify subcommand validates dashboard queries and correlation searches against a live or test Splunk REST endpoint read-only to catch syntax issues and empty result sets.

Platform: Splunk Enterprise or Splunk Cloud REST API target, Python runtime on a local host. Minimum runtime: Python 3.11, standard library only. Tested status: Locally checked; live matrix pending. No operating-system or Splunk-version compatibility has been established by these local tests.

## Prerequisites

- Python 3.11 or newer with standard library.
- Read access to local configuration files.
- Network access to a Splunk REST API endpoint (typically port 8089) for the verify command.
- A Splunk authentication token or basic authentication credentials supplied through environment variables.
- A readable custom PEM certificate authority bundle if connecting to a Splunk instance using a private CA.
- Write access to the target output directory when generating dashboard files.

## Guided setup

Run the configurator to generate a local configuration without making network calls:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/unifi-flow-dashboards"
python "$TOOL_DIR/configure.py" \
  --splunk-url https://splunk.example.net:8089 \
  --output-dir "$TOOL_DIR/dist"
```

Review every `CUSTOMIZE:` marker in `config.local.toml`. The configurator refuses to replace an existing configuration unless `--overwrite` or `--force` is given.

## Manual setup

Copy the example configuration to the ignored local filename:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/unifi-flow-dashboards"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
(umask 077; set -C; cat "$TOOL_DIR/config.example.toml" > "$CONFIG_PATH")
${EDITOR:-vi} "$CONFIG_PATH"
```

Set the authentication token in the environment:

```bash
read -r -s -p 'Splunk Auth Token: ' SPLUNK_TOKEN
export SPLUNK_TOKEN
```

Run the build command to generate the dashboard views and Splunk application skeleton locally:

```bash
python "$TOOL_DIR/unifi_flow_dashboards.py" build --config "$CONFIG_PATH"
```

Deploy the generated app directory to your Splunk instance (for example, copy `$TOOL_DIR/dist` to `$SPLUNK_HOME/etc/apps/unifi_dashboards` on the search head and reload or restart Splunk) so the target app namespace, macros, and event types are installed and accessible to searches.

Verify dashboard panel queries and correlation searches against the Splunk REST endpoint, pointing `--app-dir` to the deployed app directory or local build output:

```bash
python "$TOOL_DIR/unifi_flow_dashboards.py" verify --config "$CONFIG_PATH" --app-dir "$TOOL_DIR/dist"
unset SPLUNK_TOKEN
```

## Inputs

Command-line options take precedence over local configuration. Local configuration takes precedence over default settings. Secrets are read strictly from named environment variables, never from configuration files or command-line arguments.

| Field | Purpose and default |
|---|---|
| `splunk.url` | Splunk management REST endpoint; default `https://127.0.0.1:8089` |
| `splunk.app` | Splunk application namespace context; default `unifi_dashboards` |
| `splunk.auth_token_env` | Environment variable name holding bearer token; default `SPLUNK_TOKEN` |
| `splunk.username` | Optional basic auth username; default empty |
| `splunk.password_env` | Environment variable name holding basic auth password; default empty |
| `splunk.verify_tls` | Verify SSL certificates; default `true` |
| `splunk.ca_file` | Path to custom PEM CA certificate file; default empty |
| `splunk.timeout_seconds` | REST request timeout in seconds; default `60.0` |
| `dashboards.output_dir` | Directory for generated dashboard views and app skeleton; default `dist` |
| `dashboards.flow_index` | Destination index for flow collector events, or empty to apply no index filter beyond the token default index; default empty (`""`). When set, must match the flow collector index. |
| `dashboards.flow_sourcetype` | Sourcetype for flow events; default `unifi:flow` |
| `dashboards.event_index` | Destination index for syslog events; default `netops` |
| `dashboards.event_sourcetype` | Sourcetype for syslog events; default `cef` |
| `dashboards.external_url` | External URL for alert action links; default `https://splunk.example.net` |
| `dashboards.intrusion_exempt_sources` | List of exempt host names or IP addresses for unblocked intrusion detection; default `[]`. When empty, the exemption clause is omitted. |
| `dashboards.intrusion_exempt_rule` | IPS rule category from which exempt sources are excluded; default empty (`""`) |
| `dashboards.internal_networks` | Internal network CIDR ranges for blocked-flow detection; default `[]`. When empty, generation of the internal host blocked search is skipped with a build note. |
| `dashboards.admin_networks` | Management network CIDR ranges permitted for admin console access; default `[]`. When empty, generation of the admin unexpected network search is skipped with a build note. |
| `dashboards.scan_exempt_sources` | Sources exempt from outbound scanning and blocked intrusion detection; default `[]`. When empty, scanning exemption clauses are omitted. |
| `dashboards.intrusion_exempt_zones` | Zones exempt from blocked intrusion detection; default `[]`. When empty, zone exemption clauses are omitted. |
| `dashboards.intrusion_exempt_zone_rule` | IPS rule category from which exempt zones are excluded; default empty (`""`) |

Use `--output-dir`, `--force`, `--flow-index`, `--flow-sourcetype`, `--event-index`, `--event-sourcetype`, and `--external-url` to override build settings. Use `--splunk-url`, `--app`, `--auth-token-env`, `--username`, `--password-env`, `--ca-file`, `--insecure`, `--timeout`, `--app-dir`, `--earliest`, `--latest`, and `--fail-on-empty` to override verify settings. The `--app-dir` option specifies a local app directory containing dashboard views and saved searches to verify instead of built-in catalog queries.

## Permissions

Run this tool as an ordinary unprivileged user. No root or administrative elevation is required on the local machine.

The Splunk account used by the verify command needs read-only search permissions across the configured indexes and search macros (`search` capability). It does not require write, administrative, or object creation permissions on Splunk.

## Dry run

The verify subcommand is read-only by design. It issues synchronous searches to the Splunk REST endpoint with `exec_mode="oneshot"`, which returns query results directly in the HTTP response without creating persistent search jobs on the server, avoiding any search job deletion step. It makes no configuration changes on Splunk.

To enforce read-only execution on imported queries from `--app-dir`, verify inspects every query before submission and rejects any query containing state-changing SPL commands as whole pipeline commands: `outputlookup`, `outputcsv`, `collect`, `tscollect`, `sendemail`, `delete`, `script`, `run`, `sendalert`, `meventcollect`, `mcollect`, and `dump`. The check reads the query text only. It can't see inside a search macro, so a macro that expands to one of these commands isn't caught. Give the verify account only the `search` capability so Splunk refuses any write that slips past this check.

For the build command, target a temporary or non-production directory to preview output files before installing them into a live Splunk instance:

```bash
python unifi_flow_dashboards.py build --output-dir /tmp/preview-dashboards
```

## Changes made

- `build`: Generates Splunk Dashboard Studio JSON definitions (`unifi_flow_insights.json`, `unifi_threat_center.json`, `unifi_client_activity.json`), Splunk view XML documents under `default/data/ui/views/`, navigation XML under `default/data/ui/nav/`, search macros in `default/macros.conf`, correlation searches in `default/savedsearches.conf`, event types in `default/eventtypes.conf`, CIM tags in `default/tags.conf`, field extractions in `default/props.conf`, alert action hostname in `default/alert_actions.conf`, app manifest in `default/app.conf`, and metadata permissions in `metadata/default.meta`.
- `verify`: Sends search expressions to the Splunk search jobs REST endpoint read-only and prints query results, empty counts, or error messages. Modifies no persistent state.

## Safeguard reasoning

Dashboard generation uses mathematical layout grid calculations with fixed column widths and gutters. All panel coordinates are bound to the canvas boundaries to prevent overlapping tiles or clipped visualizations. Color assignments follow validated contrast thresholds.

The build command checks specifically for conflicting destination files that it intends to generate before writing. If any of those target files already exist in the output directory, it aborts without writing unless `--force` is explicitly provided. Each file is written using exclusive creation mode (`x`) when `--force` is not set, preventing race conditions or concurrent file creation from overwriting data.

Authentication credentials are treated as sensitive data. Secrets are never passed as command-line arguments or saved in configuration files. All error messages, server error responses, and exception strings pass through a redaction filter that replaces authentication tokens, passwords, and Authorization headers with `[REDACTED]` before printing.

## Rollback

To roll back a build operation, remove the generated output directory:

```bash
rm -rf dist/
```

Because verify executes read-only searches against Splunk, no remote rollback is necessary.

## Troubleshooting

- `refusing to overwrite existing files in ...`: The destination output directory already contains files from a previous run. Specify `--force` to permit overwriting, or choose a different `--output-dir`.
- `authentication-error: neither token nor password found`: Ensure the environment variable named in `auth_token_env` (default `SPLUNK_TOKEN`) or `password_env` is exported in your shell session.
- `HTTP 401: Unauthorized`: The token or basic authentication credentials provided are invalid or expired.
- `HTTP 403: Forbidden`: The authenticated Splunk user lacks search permissions or access to the specified app namespace.
- `HTTP 404: Not Found`: The specified app context namespace does not exist on the target Splunk instance. Verify the `--app` argument.
- Certificate verification failed: When using a self-signed or internal CA on Splunk management port 8089, pass the CA certificate with `--ca-file path/to/ca.pem` or set `verify_tls = false` in `config.local.toml` for local testing.

## Exit behavior

- `0`: Operation completed successfully. For build, all files were generated. For verify, all dashboard and correlation queries completed without search execution errors.
- `1`: Configuration parsing error, destination file overwrite conflict, missing environment credentials, unreadable or malformed dashboard definitions, missing results array, forbidden SPL command detected, or one or more queries failed execution.
- `2`: Command-line argument parsing error (e.g. unknown options, missing required arguments, or invalid parameter values).
