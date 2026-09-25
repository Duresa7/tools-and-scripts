# UniFi flow collector

Poll UniFi traffic flows, map them to CIM field names, and preview or send event envelopes to Splunk HEC.

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

Collect the connection records behind UniFi Insights: source and destination, allow or block decisions, protocol, application, risk, policy, byte counts, and packet counts. The collector uses the controller's private v2 traffic-flow endpoint. Endpoint availability and API-key access depend on the installed controller release. Session authentication remains available when API keys cannot access that endpoint.

Platform: Python runtime on a local host; the service example targets Linux with systemd. Minimum runtime: Python 3.11, standard library only. Tested status: Locally checked; live matrix pending. No operating-system or controller-version compatibility has been established by these local tests.

## Prerequisites

- A controller exposing `/proxy/network/v2/api/site/{site}/traffic-flows`.
- An API key authorized to read flows, or an ordinary local controller account with that access.
- An enabled HEC event endpoint and token authorized for an existing destination index.
- Readable PEM CA files if either endpoint uses a private CA.
- An existing writable directory for the checkpoint when sending events.

## Guided setup

Run setup as an ordinary user. It creates only local configuration and makes no network requests. Preview reads the controller but sends nothing to HEC and writes no checkpoint. Sending requires `--send`; accepted HEC events cannot be rolled back by this tool. Exit codes are 0 for success, 1 for input failure, 2 for command syntax, and 3 for a failed single poll.

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/unifi-flow-collector"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
python "$TOOL_DIR/configure.py" \
  --controller-url https://unifi.example.net \
  --hec-url https://splunk.example.net:8088/services/collector/event
${EDITOR:-vi} "$CONFIG_PATH"
```

Review every `CUSTOMIZE:` marker. The configurator writes mode `0600` on POSIX and refuses to replace an existing file. Use the separate `--overwrite` option only when replacing the configuration is intended. `--output PATH` selects another destination. Default output is beside the script, regardless of the working directory.

## Manual setup

Copy the [example TOML](config.example.toml) to the ignored local name, then edit it:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/unifi-flow-collector"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
(umask 077; set -C; cat "$TOOL_DIR/config.example.toml" > "$CONFIG_PATH")
${EDITOR:-vi} "$CONFIG_PATH"
read -r -s -p 'UniFi API key: ' UNIFI_API_KEY
export UNIFI_API_KEY
read -r -s -p 'HEC token: ' SPLUNK_HEC_TOKEN
export SPLUNK_HEC_TOKEN
python "$TOOL_DIR/unifi_flow_collector.py" --config "$CONFIG_PATH" --once --dry-run
```

The collector reads secrets from the named process environment variables, never from TOML values or command arguments. It does not read an env file itself. Clear the variables with `unset UNIFI_API_KEY SPLUNK_HEC_TOKEN` after use.

For session authentication, set `unifi.api_key_env = ""`, set `unifi.username` to the chosen ordinary account, and set `unifi.password_env = "UNIFI_PASSWORD"`. Supply that password through a hidden prompt or service credential manager. Session cookies and CSRF tokens are kept in memory. A rejected session triggers one fresh login and retry.

## Inputs

Command-line overrides take precedence over local configuration. Local configuration takes precedence over defaults. `--config` explicitly selects the TOML file. Relative TOML paths resolve from its directory; relative command-line paths resolve from the working directory. Unknown config keys and wrong value types are rejected.

| TOML fields | Purpose and default |
|---|---|
| `unifi.controller_url`, `site` | Console base URL is required; site defaults to `default` |
| `unifi.api_key_env` | API-key environment-variable name; default `UNIFI_API_KEY` |
| `unifi.username`, `password_env` | Optional session account and password-variable name; both empty by default |
| `unifi.verify_tls`, `ca_file` | Verify certificates by default; empty CA uses system trust |
| `hec.url`, `token_env` | Default `https://127.0.0.1:8088/services/collector/event`; variable `SPLUNK_HEC_TOKEN` |
| `hec.index`, `sourcetype` | Empty index uses token default; sourcetype defaults to `unifi:flow` |
| `hec.source`, `event_host` | Default source `unifi:traffic-flows`; empty host uses controller hostname |
| `hec.verify_tls`, `ca_file` | Verify certificates by default; empty CA uses system trust |
| `timeout_seconds` in either connection table | Request timeout, default 60 seconds |
| `collector.poll_interval_seconds` | Default 120 seconds |
| `collector.lookback_seconds` | Initial history window, default 900 seconds |
| `collector.page_size` | Default 1000 flows per page |
| `collector.state_path` | Default `checkpoint.json` beside the config |

Use `--controller-url`, `--site`, `--api-key-env`, `--username`, `--password-env`, `--unifi-ca-file`, `--hec-url`, `--hec-token-env`, `--hec-ca-file`, `--index`, `--sourcetype`, `--poll-interval`, and `--state-file` for overrides. A CLI API-key override clears session settings; a session override clears API-key mode. TLS verification policy is set in TOML. A CA file requires HTTPS with verification enabled. Plain HTTP is accepted only for loopback testing. Never put credentials in URLs.

## Permissions

Run the collector as an ordinary user with read access to configuration and CA files, network access to both endpoints, and write access to the checkpoint directory. No host elevation is required for polling.

The [systemd unit](examples/unifi-flow-collector.service) uses a dedicated unprivileged account. Customize the account, paths, and state directory before installing it. Create that service account through your host's normal account-management process. Systemd creates and owns the configured state directory for that account. Keep script and config directories readable but not writable by the service account.

The [environment example](examples/credentials.env.example) contains empty placeholders. Have your credential manager populate a mode `0600` file outside the repository at the unit's `EnvironmentFile` path. Systemd reads it before dropping privileges. Ensure its variable names match the TOML fields. Do not commit the populated file. Install the reviewed unit under `/etc/systemd/system/`, apply elevation only for those system writes, run `sudo systemctl daemon-reload`, and enable it with `sudo systemctl enable --now unifi-flow-collector.service` after a successful preview. The example unit explicitly enables sending.

## Dry run

```bash
python "$TOOL_DIR/unifi_flow_collector.py" --config "$CONFIG_PATH" --once --dry-run
```

`--stdout` is an alias. Preview is also the default when `--send` is absent. It prints one mapped HEC envelope per line, with operational logs on stderr. It contacts UniFi and may create a login session, but does not contact HEC, require its token, create a lock, or modify any checkpoint. Existing checkpoint IDs still filter the preview. Without `--once`, previews repeat until interrupted.

Output includes actual flow addresses and names. Store any captured output outside the public repository.

## Changes made

```bash
python "$TOOL_DIR/unifi_flow_collector.py" --config "$CONFIG_PATH" --once --send
```

`--send` posts batches to HEC and atomically replaces the checkpoint after accepted delivery. Omit `--once` for continuous polling. It creates a checkpoint lock file and preserves an invalid checkpoint under a unique `.invalid-` filename before collecting again. It does not configure the controller, create an index, install dashboards, or modify services. Service installation is a separate manual step.

## Safeguard reasoning

Preview is the default because HEC ingestion creates remote events. TLS verification defaults to enabled and redirects are rejected to keep credentials at their configured endpoints. Response bodies are excluded from errors; HEC errors show only HTTP status and a numeric result code.

Each poll rereads 300 seconds behind the newest accepted flow. Up to 40,000 remembered flow IDs suppress repeats, including duplicates within one poll. Batches contain at most 250 events. A page cap of 200 stops collection without sending or advancing state, so a truncated query cannot silently skip the rest of that window.

Only HEC responses with integer code 0 count as accepted. If a later batch fails, the checkpoint keeps IDs from accepted batches but retains the previous time boundary. A network failure after HEC accepts a batch, or a checkpoint-write failure, can still cause duplicates on retry. This is not exactly-once delivery and does not use HEC indexer acknowledgments. The bounded ID history and controller retention also limit recovery after long outages.

Checkpoint writes use a temporary file, flush, fsync, and atomic replacement. A POSIX file lock rejects concurrent writers. On runtimes without `fcntl`, locking is unavailable; arrange exactly one process per checkpoint.

## Rollback

Stop the foreground process or use `sudo systemctl disable --now unifi-flow-collector.service` before changing its configuration. Keep the checkpoint to resume without resending remembered flows. Restoring or deleting a checkpoint can replay records; deleting it restarts with only the configured lookback window. Keep quarantined checkpoints for inspection.

The tool cannot remove accepted HEC events. Any index cleanup is a separate administrative action. Remove the installed unit and reload systemd if retiring the service. Delete local configuration and credentials only after collection has stopped.

## Troubleshooting

- `input-error`: inspect field names, paths, credential-variable names, and directory permissions. Configuration validation makes no remote request.
- HTTP 401 or 403: confirm flow access for the API key or session account. Do not paste credentials into logs.
- HTTP 3xx: configure the final endpoint; redirects are refused.
- HEC rejection: check the endpoint, token permissions, index, and numeric HEC code. Failed batches remain eligible for retry.
- TLS request failure: confirm the certificate hostname and trusted CA. Prefer adding the CA over disabling verification.
- Page cap reached: increase page size or reduce initial lookback. Inspect controller retention before resetting state.
- Another collector holds the lock: stop the other process; do not unlink its lock while it runs.
- Repeated service errors: inspect `journalctl -u unifi-flow-collector.service`. Continuous mode retries with delays from 30 to 600 seconds.

Run local checks from the repository root with `python -m pytest -q monitoring/unifi-flow-collector/tests`. Tests use only local fake HTTP servers.

## Exit behavior

- `0`: completed a successful single poll, stopped on SIGINT or SIGTERM after the current poll, or a preview reader closed its pipe.
- `1`: invalid or unreadable configuration, missing credential, TLS setup failure, or unavailable checkpoint directory or lock.
- `2`: command-line usage error, including combining `--send` with `--dry-run`.
- `3`: a poll failed under `--once`, including HTTP, HEC, malformed response, page-cap, or checkpoint-write failure.

Continuous mode logs poll failures and retries until stopped. The configurator returns 0 after writing, 1 on validation or write failure, and 2 for usage errors.
