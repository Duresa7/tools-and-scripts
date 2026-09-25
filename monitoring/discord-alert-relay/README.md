# Discord alert relay

Receive Grafana and Splunk alert webhooks and relay them as formatted embeds to Discord.

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

Monitoring systems like Grafana and Splunk need to deliver actionable alert notifications to a Discord channel. Grafana sends webhook alerts with a bearer token, while Splunk's webhook alert action cannot send headers and must be authenticated by source IP address.

This service acts as an HTTP relay:
1. Accepts Grafana alert webhooks on `POST /grafana` with a bearer secret.
2. Accepts Splunk webhook alerts on `POST /splunk` guarded by an IP address allowlist.
3. Groups and maps alerts to Discord embeds with class styling (prefixes and colors), condensing repeated alerts when a threshold is met.
4. Posts embeds to Discord using standard REST API calls (Bot token or incoming webhook URL). Automatically handles HTTP 429 rate limits by waiting up to a configurable cap and retrying once.
5. Returns HTTP 200 when every embed posted, or HTTP 502 with `{"posted": n, "alerts": total}` when any embed failed so upstream systems retry delivery.
6. Serves `GET /health`, returning 200 only after confirming Discord API reachability. Automatically marks the relay not ready on 401, 403, or network errors, and re-checks readiness periodically so health recovers when Discord returns to service.

Tested status: Locally checked; live matrix pending.

| Field | Required answer |
|---|---|
| Platform | Linux, macOS, or Windows with Python 3.11+; or container runtime (Docker / Podman) |
| Privilege | Ordinary user or unprivileged container user |
| State change | Binds local HTTP listening port; posts messages to Discord channel or webhook |
| Preview | `--dry-run` validates config, credentials, and Discord connectivity without running the server |
| Rollback | Stop the process or container; delete `config.local.toml` if created |
| Exit codes | `0` on success or clean shutdown, `1` on configuration or startup error, `2` on invalid arguments |

## Prerequisites

- Python 3.11 or newer (Python standard library only).
- Network access to Discord REST API (`https://discord.com/api/v10` or webhook URL).
- A Discord bot token and channel ID, or an incoming webhook URL.
- A shared secret for Grafana webhook authentication.

## Guided setup

The configurator writes an ignored `config.local.toml` file and refuses to overwrite an existing configuration without `--overwrite`.

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/discord-alert-relay"
python "$TOOL_DIR/configure.py"
```

To configure specific listen ports and allowlisted Splunk senders:

```bash
python "$TOOL_DIR/configure.py" \
  --listen-port 8080 \
  --splunk-source "127.0.0.1" \
  --splunk-source "192.0.2.10" \
  --splunk-source "198.51.100.0/24"
```

To regenerate the configuration and overwrite an existing file:

```bash
python "$TOOL_DIR/configure.py" --overwrite
```

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/discord-alert-relay"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
cp "$TOOL_DIR/config.example.toml" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

Set environment variables holding credentials:

```bash
read -r -s -p "Grafana bearer secret: " ALERT_RELAY_SECRET
export ALERT_RELAY_SECRET
echo

read -r -s -p "Discord bot token: " DISCORD_BOT_TOKEN
export DISCORD_BOT_TOKEN
echo

python "$TOOL_DIR/alert_relay.py" --config "$CONFIG_PATH"
```

PowerShell (`Read-Host -MaskInput` requires PowerShell 7.1 or newer):

```powershell
$ToolDir = Join-Path $HOME "tools-and-scripts/monitoring/discord-alert-relay"
$ConfigPath = Join-Path $ToolDir "config.local.toml"
Copy-Item (Join-Path $ToolDir "config.example.toml") $ConfigPath
notepad $ConfigPath

$env:ALERT_RELAY_SECRET = Read-Host "Grafana bearer secret" -MaskInput
$env:DISCORD_BOT_TOKEN = Read-Host "Discord bot token" -MaskInput

py (Join-Path $ToolDir "alert_relay.py") --config $ConfigPath
```

Review and update every `CUSTOMIZE:` marker.

## Inputs

The TOML configuration controls network binding, delivery mode, class prefixes, colors, and allowlists:

- `[server]`: `listen_address`, `listen_port`, `max_request_bytes`, `secret_env`, `allowed_splunk_sources`.
- `[discord]`: `mode` ("bot" or "webhook"), `bot_token_env`, `channel_id`, `webhook_url_env`, `api_base_url`, `timeout_seconds`, `retry_after_cap_seconds`, `readiness_interval_seconds`.
- `[alerts]`: `default_class`, `condense_threshold`, `resolved_color`.
- `[classes.<name>]`: `prefix`, `color` for each alert class.
- `[severity_colors]`: `critical`, `warning`, `info` colors.

Key command-line options:
- `--listen-address`: server bind address.
- `--listen-port`: server TCP port.
- `--retry-after-cap`: maximum wait time in seconds for HTTP 429 rate limit retry (default 5.0).
- `--readiness-interval`: interval in seconds between periodic Discord readiness checks (default 60.0).
- `--dry-run` / `--validate`: test configuration and credentials against Discord without starting the listener.

Command-line flags override configuration file settings. Configuration fields store environment variable names rather than secrets.

## Permissions

Run the relay service as an ordinary unprivileged user. No root privileges or elevated administrative rights are required. The process only requires permission to bind the configured TCP port and initiate outbound HTTPS connections to Discord.

When deploying with Docker, the container runs under unprivileged system user `relay` (`uid 10001`).

## Dry run

Test configuration validity, environment variable presence, and Discord API reachability without starting the server:

```bash
python "$TOOL_DIR/alert_relay.py" --config "$CONFIG_PATH" --dry-run
```

If credentials and API checks succeed, the process prints readiness confirmation and exits with code 0.

## Changes made

The relay binds the specified TCP port and creates no temporary files on disk. When alerts arrive from Grafana or Splunk, it sends HTTP requests to Discord's API to post embed messages into the designated channel or webhook. If any embed fails to deliver, the service returns HTTP 502 with a JSON payload reporting the count of successfully posted embeds and total alerts, allowing senders to retry.

## Safeguard reasoning

- Webhook authorization: Grafana requests require an `Authorization: Bearer <secret>` header. Verification uses `hmac.compare_digest` to prevent timing attacks.
- Source address allowlist: Splunk webhook payloads cannot include authentication headers. The service enforces an IP allowlist using Python's `ipaddress` module, answering 403 Forbidden to any source not explicitly permitted.
- Size limits: Inbound HTTP request bodies are limited by `max_request_bytes` (default 1 MiB) to prevent memory exhaustion.
- Discord embed boundaries: Discord rejects embeds exceeding 1024 characters per field or 6000 characters total. The relay measures fields, omits oversized silence links, and trims long descriptions to fit safely within Discord limits.
- Validated URLs: Discord rejects embeds whose URLs do not contain a domain dot. Links are validated before inclusion.
- Delivery retry signal: When Discord delivery fails for any embed, the relay responds with HTTP 502 Bad Gateway and `{"posted": n, "alerts": total}` instead of HTTP 200, signalling Grafana and Splunk to retry delivery.
- Rate-limit backoff: On Discord HTTP 429 responses, the service checks `Retry-After` (from header or response body). If the wait time is at most `retry_after_cap_seconds` (default 5 seconds), it pauses and retries once. If the wait exceeds the cap, it immediately counts the post as a failure.
- Active health lifecycle: The relay dynamically marks itself not ready when any Discord request returns 401 Unauthorized, 403 Forbidden, or fails with a network exception, answering 503 on `GET /health`. A background poller re-checks Discord readiness every `readiness_interval_seconds` (default 60 seconds), restoring 200 on `/health` once Discord recovers.
- Credential privacy: Secrets and tokens are read exclusively from environment variables and are never written to disk or printed in log output.

## Rollback

Stop the running process using `Ctrl+C` or send `SIGTERM`. If running via Docker Compose, run `docker compose down`. To remove local configuration, delete `config.local.toml`. The tracked `config.example.toml` remains unchanged.

## Troubleshooting

- `required environment variable ... is not set or empty`: Ensure the environment variable named in the configuration is exported in the shell before starting the relay.
- `rejected webhook from ...: bad or missing bearer`: Verify the Grafana contact point bearer token matches the value in the secret environment variable.
- `rejected splunk webhook from ...: address not in allowlist`: Add the Splunk server IP address or subnet CIDR to `allowed_splunk_sources` in the configuration.
- `HTTP 502 Bad Gateway`: Discord rejected an embed or delivery failed. Check relay logs for API errors or rate-limit warnings.
- `discord not ready` on `GET /health`: The relay has not yet completed a successful check against Discord's API, or recently encountered a 401, 403, or network failure. Verify network connectivity, channel ID, and bot token or webhook URL. The relay re-checks readiness periodically.
- `payload too large`: The incoming webhook body exceeded `max_request_bytes`. Increase the limit in configuration if large batches are expected.

## Exit behavior

- `0`: Clean shutdown after receiving `SIGINT` or `SIGTERM`, or successful `--dry-run` verification.
- `1`: Configuration error, missing required credential environment variable, bind failure, or dry-run connectivity failure.
- `2`: Invalid command-line arguments.
