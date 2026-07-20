# Prometheus target check

Compare Prometheus `/api/v1/targets` with an exact expected set. The command reports missing, duplicate, unexpected, forbidden, and unhealthy targets in one run.

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

Use this after a Prometheus migration, target replacement, DNS change, or scrape configuration edit. A green Prometheus process isn't enough; the target API can still contain an old address, a duplicate scrape, or a target whose last scrape failed.

The tool runs on Linux, macOS, and Windows with Python 3.11 or newer. It supports HTTP, HTTPS with the system trust store, HTTPS with a private CA, bearer tokens, and basic authentication.

## Prerequisites

- Python 3.11 or newer.
- Network access to the Prometheus endpoint unless checking a saved response.
- Read access to the custom CA file when one is configured.
- A bearer token or basic-auth password in a named environment variable when authentication is enabled.

## Guided setup

The configurator writes ignored `config.local.json` and refuses to replace an existing file. Without `--discover-targets`, it makes no network request and leaves one documentation target for you to replace.

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/prometheus-target-check"
python "$TOOL_DIR/configure.py"
```

Remote discovery requires an explicit flag:

```bash
PROMETHEUS_URL="https://prometheus.example.net/api/v1/targets"
python "$TOOL_DIR/configure.py" --url "$PROMETHEUS_URL" --discover-targets
```

That request reads active targets and seeds the expectation list. It doesn't edit Prometheus.

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/prometheus-target-check"
CONFIG_PATH="$TOOL_DIR/config.local.json"
cp "$TOOL_DIR/config.example.json" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
python "$TOOL_DIR/check_targets.py" --config "$CONFIG_PATH"
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/monitoring/prometheus-target-check'
$ConfigPath = Join-Path $ToolDir 'config.local.json'
Copy-Item (Join-Path $ToolDir 'config.example.json') $ConfigPath
notepad $ConfigPath
py (Join-Path $ToolDir 'check_targets.py') --config $ConfigPath
```

Replace every `CUSTOMIZE:` value. `expectations.targets` must list each `(job, scrape_url)` pair exactly once.

## Inputs

The JSON config contains the endpoint, timeout, optional CA path, authentication mode, expected targets, forbidden URL substrings, and required health value. Credential fields hold environment-variable names, never tokens or passwords.

Command-line values for `--url`, `--timeout`, `--ca-file`, and authentication settings override the config. Use `--no-ca-file` to return to the system trust store and `--no-auth` to disable configured authentication. The config overrides the default URL `http://127.0.0.1:9090/api/v1/targets` and the 10-second timeout. `--input PATH` checks a saved API response instead of making an HTTP request.

Bearer-token example:

```bash
read -r -s -p 'Prometheus token: ' PROMETHEUS_TOKEN
export PROMETHEUS_TOKEN
python "$TOOL_DIR/check_targets.py" --config "$CONFIG_PATH"
unset PROMETHEUS_TOKEN
```

```powershell
$env:PROMETHEUS_TOKEN = Read-Host 'Prometheus token' -MaskInput
py (Join-Path $ToolDir 'check_targets.py') --config $ConfigPath
Remove-Item Env:PROMETHEUS_TOKEN
```

Set `bearer_token_env` to `PROMETHEUS_TOKEN`. For basic auth, set `basic_username` and `basic_password_env`; leave the bearer field empty. The two modes can't be enabled together.

## Permissions

Run the check as an ordinary user. No elevation is required. The account needs read access to the config and CA file plus network access to Prometheus.

## Dry run

The command is read-only. To test expectations without contacting Prometheus, save a `/api/v1/targets` response and pass it explicitly:

```bash
RESPONSE_PATH="$HOME/prometheus-targets.json"
python "$TOOL_DIR/check_targets.py" --config "$CONFIG_PATH" --input "$RESPONSE_PATH"
```

## Changes made

The validator changes no Prometheus configuration, target, or local file. The configurator creates only the requested local JSON file. It refuses to overwrite that file.

## Safeguard reasoning

An exact set comparison catches both missing and extra targets. Duplicate counting catches the same job and URL scraped twice. Redirects are rejected so an authorization header isn't forwarded to a second endpoint. Error messages report the configured environment-variable name, never its value.

## Rollback

No runtime rollback is needed because the validator is read-only. Delete `config.local.json` if you no longer need the local configuration; the tracked example remains unchanged.

## Troubleshooting

- `environment variable ... is empty or unset`: set the named variable in the same terminal that runs the command.
- `custom CA file not found`: use an absolute PEM path that the current account can read.
- `Prometheus returned HTTP 3xx`: use the final endpoint URL; redirects are rejected.
- `missing targets` or `unexpected targets`: compare the printed job and scrape URL rows with `expectations.targets`.
- `forbidden scrape URL values`: remove the stale target from Prometheus or correct the forbidden list.

## Exit behavior

- `0`: the response parsed and every target assertion passed.
- `1`: invalid config, missing credential environment variable, TLS or HTTP failure, malformed JSON, or unreadable input.
- `2`: the API response was valid but one or more target assertions failed.
