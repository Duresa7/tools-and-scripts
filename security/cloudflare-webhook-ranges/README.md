# Cloudflare webhook ranges

Keep a Cloudflare Zero Trust IP list equal to the webhook source ranges GitHub publishes, so an Access bypass policy for a webhook receiver can match GitHub and nothing else.

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

A webhook receiver behind Cloudflare Access needs a Bypass policy, because GitHub can't complete an Access login and can't send custom headers for a service token. A Bypass that includes Everyone leaves the receiver open to the whole internet, and Cloudflare's Security Insights flags it as an overprovisioned policy. The fix is a Bypass whose include rule is an IP list holding GitHub's webhook ranges.

GitHub changes those ranges a few times a year and publishes the current set in the `hooks` key of `https://api.github.com/meta`. A range the list lacks fails quietly: the delivery dies at Access, and the only sign is a failed row on the GitHub App's Recent Deliveries page. This tool reads the published ranges, compares them with the list, and sends one update for the difference. It also writes a node_exporter textfile with the time of its last successful run, so a Prometheus or Grafana rule can tell you when the sync has stopped.

Tested status: Locally checked; live matrix pending.

| Field | Required answer |
|---|---|
| Platform | Linux, macOS, or Windows with Python 3.11+; the systemd units need Linux |
| Privilege | Ordinary user holding a Cloudflare API token with Zero Trust Edit on one account |
| State change | Items appended to or removed from one named Zero Trust IP list; one `.prom` file at `output_path` |
| Preview | `--dry-run` prints the ranges that would be added and removed and changes nothing |
| Rollback | Disable the timer; the list keeps its last synced contents |
| Exit codes | `0` success or no change, `1` configuration, sync, or write error, `2` invalid arguments |

## Prerequisites

- Python 3.11 or newer, standard library only.
- A Zero Trust IP list that already exists, created in the Cloudflare dashboard under Zero Trust, My Team, Lists. The tool finds it by name and never creates or deletes a list.
- An Access policy whose include rule is that list. The tool never touches policies.
- A Cloudflare account API token with the single permission Zero Trust Edit on the account that owns the list. Gateway lists sit under Zero Trust, so Access permissions alone don't reach them.
- Outbound HTTPS to `api.github.com` and `api.cloudflare.com`.
- Optional: Prometheus node_exporter with `--collector.textfile.directory` on the host that runs the sync.

## Guided setup

The configurator writes an ignored `config.local.toml` and refuses to overwrite an existing file without `--overwrite`. It never asks for the token and never writes one.

```bash
TOOL_DIR="$HOME/tools-and-scripts/security/cloudflare-webhook-ranges"
python "$TOOL_DIR/configure.py" --list-name "GitHub Webhooks"
```

To regenerate the configuration:

```bash
python "$TOOL_DIR/configure.py" --overwrite
```

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/security/cloudflare-webhook-ranges"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
cp "$TOOL_DIR/config.example.toml" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
read -r -s -p "Cloudflare API token: " CLOUDFLARE_API_TOKEN; echo
export CLOUDFLARE_API_TOKEN
python "$TOOL_DIR/cloudflare_webhook_ranges.py" --config "$CONFIG_PATH" --dry-run
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME "tools-and-scripts/security/cloudflare-webhook-ranges"
$ConfigPath = Join-Path $ToolDir "config.local.toml"
Copy-Item (Join-Path $ToolDir "config.example.toml") $ConfigPath
notepad $ConfigPath
$Secure = Read-Host -Prompt "Cloudflare API token" -AsSecureString
$env:CLOUDFLARE_API_TOKEN = [Net.NetworkCredential]::new("", $Secure).Password
py (Join-Path $ToolDir "cloudflare_webhook_ranges.py") --config $ConfigPath --dry-run
```

Review every `CUSTOMIZE:` marker.

### Systemd timer scheduling

Install the script, the configuration, and a root-only token file, then copy the two units from `systemd/` and adjust their `CUSTOMIZE:` lines:

```bash
TOOL_DIR="$HOME/tools-and-scripts/security/cloudflare-webhook-ranges"
sudo install -d -m 750 /etc/cloudflare-webhook-ranges
sudo install -m 755 "$TOOL_DIR/cloudflare_webhook_ranges.py" /usr/local/bin/cloudflare_webhook_ranges.py
sudo install -m 644 "$TOOL_DIR/config.local.toml" /etc/cloudflare-webhook-ranges/config.local.toml
( umask 077; read -r -s -p "Cloudflare API token: " T; echo; printf 'CLOUDFLARE_API_TOKEN=%s\n' "$T" | sudo tee /etc/cloudflare-webhook-ranges/token.env > /dev/null )
sudo chmod 600 /etc/cloudflare-webhook-ranges/token.env
sudo install -m 644 "$TOOL_DIR/systemd/cloudflare-webhook-ranges.service" /etc/systemd/system/
sudo install -m 644 "$TOOL_DIR/systemd/cloudflare-webhook-ranges.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflare-webhook-ranges.timer
```

The service unit reads the token through `EnvironmentFile`, runs as root so the `0600` token file stays root-only, and is sandboxed with `ProtectSystem=strict`, `ProtectHome=true`, and `NoNewPrivileges=true`, with the textfile directory as its only writable path. The timer runs daily at 06:20 with up to ten minutes of random delay and catches up after downtime.

## Inputs

The TOML configuration holds the GitHub source, the Cloudflare target, and the metrics output:

```toml
[github]
# CUSTOMIZE: The document that publishes the hooks ranges.
meta_url = "https://api.github.com/meta"
# CUSTOMIZE: Refuse to sync when GitHub publishes fewer ranges than this.
minimum_ranges = 3

[cloudflare]
# CUSTOMIZE: Cloudflare API base URL.
api_url = "https://api.cloudflare.com/client/v4"
# CUSTOMIZE: Account identifier. Leave empty when the token sees one account.
account_id = ""
# CUSTOMIZE: Name of the Zero Trust IP list to keep in sync.
list_name = "GitHub Webhooks"
# CUSTOMIZE: Request timeout in seconds.
timeout_seconds = 20.0

[metrics]
# CUSTOMIZE: Destination .prom file for the node_exporter textfile collector.
output_path = "/var/lib/prometheus/node-exporter/cloudflare-webhook-ranges.prom"
# CUSTOMIZE: Prefix for the exported metrics.
metric_prefix = "cloudflare_webhook_ranges"
```

The token comes only from the `CLOUDFLARE_API_TOKEN` environment variable. A configuration that carries an `api_token` or `token` key is rejected.

Command-line options override configuration values:

- `--config PATH`: path to the TOML configuration; defaults to `config.local.toml` beside the script when present.
- `--meta-url URL`: overrides `github.meta_url`.
- `--api-url URL`: overrides `cloudflare.api_url`.
- `--account-id ID`: overrides `cloudflare.account_id`.
- `--list-name NAME`: overrides `cloudflare.list_name`.
- `--timeout SECONDS`: overrides `cloudflare.timeout_seconds`.
- `--output PATH`: overrides `metrics.output_path`.
- `--dry-run`: reports the difference and changes nothing.
- `--stdout`: prints the metrics instead of writing the textfile.

Precedence: command line, then explicit local configuration, then documented defaults.

## Permissions

The Cloudflare token needs Zero Trust Edit on the one account that owns the list, and nothing else. Scope it to that account only, with no expiry if it runs unattended, because an expired token fails as quietly as a missing range. When the token sees exactly one account, `account_id` can stay empty and the tool reads the account from `GET /accounts`.

The process needs read access to the configuration, the token in its environment, and write access to the directory holding `output_path`. No elevation is needed to run the sync itself; the systemd example runs as root only so the token file can stay `0600` and root-owned.

## Dry run

```bash
CLOUDFLARE_API_TOKEN="$TOKEN" python "$TOOL_DIR/cloudflare_webhook_ranges.py" --dry-run
```

A dry run reads both sides, prints `would add [...], removed [...]` or `N ranges, no change`, sends no `PATCH`, and writes no metrics file. The metrics preview is separate:

```bash
CLOUDFLARE_API_TOKEN="$TOKEN" python "$TOOL_DIR/cloudflare_webhook_ranges.py" --stdout
```

`--stdout` runs the real sync and prints the metrics instead of writing them.

## Changes made

- One `PATCH /accounts/{account}/gateway/lists/{list}` with the ranges to append and remove, sent only when the two sets differ.
- One `.prom` file at `output_path`, written through a temporary file in the same directory and moved into place with `os.replace`, mode `0644`. It holds five gauges: `last_run_ok`, `count`, `changed`, `last_success_timestamp_seconds`, and `last_run_timestamp_seconds`, each under `metric_prefix`.
- `configure.py` creates only `config.local.toml`.

Nothing else is touched: no list is created or deleted, no Access policy or application is read or written, and the GitHub side is read-only.

## Safeguard reasoning

- Minimum range count: an empty or truncated meta document would otherwise empty the list and lock every webhook out. Fewer than `minimum_ranges` entries is treated as a broken response and the run fails without a `PATCH`.
- Normalised ranges: every entry is parsed as a network before comparison, so a formatting difference between GitHub and Cloudflare never shows up as a change.
- Read-back after update: the list is read again after the `PATCH` and the run fails if it doesn't match the published set, so a partially applied update can't pass as success.
- Token out of band: the token lives in an environment variable, never in the configuration, the command line, or the metrics file, and the configuration parser rejects a token key.
- Preserved success time: a failed run writes `last_run_ok 0` and carries the previous `last_success_timestamp_seconds` forward, so an alert on the age of the last success fires on the day the sync broke rather than resetting.
- Atomic textfile: node_exporter never scrapes a half-written file.

## Rollback

```bash
sudo systemctl disable --now cloudflare-webhook-ranges.timer
```

The list keeps whatever the last sync left in it; nothing needs restoring. To undo a specific sync, re-add the removed ranges by hand in the dashboard under Zero Trust, My Team, Lists. To remove the tool, delete `/usr/local/bin/cloudflare_webhook_ranges.py`, `/etc/cloudflare-webhook-ranges/`, the two units, and the `.prom` file, then revoke the token in the Cloudflare dashboard.

## Troubleshooting

- `configuration-error: CLOUDFLARE_API_TOKEN is not set`: export the variable, or check the `EnvironmentFile` path in the service unit.
- `sync-error: token sees N accounts; set account_id`: the token is scoped to more than one account, or to none. Set `cloudflare.account_id`, or narrow the token.
- `sync-error: Cloudflare GET /accounts/.../gateway/lists?type=IP failed: [{'code': 10000, ...}]`: the token lacks Zero Trust Edit, or is scoped to the wrong account.
- `sync-error: IP list 'GitHub Webhooks' not found`: the name in `list_name` doesn't match a list of type IP on that account. Names are case-sensitive.
- `sync-error: GitHub returned N hook ranges, fewer than the minimum`: GitHub answered with a short or empty `hooks` list. Nothing was changed. Check `meta_url` and try again later.
- `sync-error: list holds [...] after the update, wanted [...]`: the `PATCH` was accepted but the list doesn't match. Run again; if it repeats, compare the list in the dashboard with the meta document.
- Webhook deliveries still fail after a sync: check the delivery's response on the GitHub App's Advanced page. A Cloudflare Access error page means the source wasn't in the list at delivery time; a Cloudflare challenge page means a zone-level bot rule is in the way, which this tool doesn't manage.

## Exit behavior

- `0`: the list matches the published ranges, after an update or without one. In a dry run, `0` also when a difference was reported.
- `1`: invalid configuration, missing token, a failed GitHub or Cloudflare request, a short meta document, a read-back mismatch, or a failed metrics write. A sync failure outside a dry run still writes the metrics file with `last_run_ok 0`.
- `2`: invalid command-line arguments, from argparse.
