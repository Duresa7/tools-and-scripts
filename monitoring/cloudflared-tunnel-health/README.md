# Cloudflare Tunnel health check

Export Cloudflare Tunnel HA connection count and local origin health to a Prometheus node_exporter textfile.

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

Cloudflare Tunnel (`cloudflared`) connects internal backend services to the Cloudflare edge without opening public inbound firewall ports. If the connector service stops, ingress traffic fails. Even if the connector process is active, individual local origin services behind the tunnel can fail independently.

This tool periodically inspects:
1. Active Cloudflare Tunnel HA connections from the connector metrics endpoint.
2. HTTP and HTTPS health status for each configured backend origin.

It writes a standard Prometheus textfile (`*.prom`) consumed by `node_exporter`. A stopped connector service reports 0 connections, while an unreachable or broken metrics endpoint reports -1 (unknown). This prevents false claims of tunnel outage when only the monitoring port is unavailable.

Tested status: Locally checked; live matrix pending.

| Field | Required answer |
|---|---|
| Platform | Linux with systemd and Python 3.11+ |
| Privilege | Ordinary user or service account with write access to textfile directory |
| State change | Atomically writes or updates one `.prom` file at `output_path` |
| Preview | `--stdout` prints metrics without touching the filesystem |
| Rollback | Remove the generated `.prom` textfile and disable the timer |
| Exit codes | `0` on success, `1` on configuration or write error, `2` on invalid arguments |

## Prerequisites

- Linux host running `cloudflared` managed as a systemd unit.
- Python 3.11 or newer (standard library only).
- Prometheus `node_exporter` configured with `--collector.textfile.directory`.
- Network access to the local cloudflared metrics endpoint (default `http://127.0.0.1:20241/metrics`).
- Network access from the collector host to all configured local origin URLs.

## Guided setup

The configurator writes an ignored `config.local.toml` file and refuses to overwrite an existing configuration without `--overwrite`.

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/cloudflared-tunnel-health"
python "$TOOL_DIR/configure.py"
```

To configure specific origin checks during setup:

```bash
python "$TOOL_DIR/configure.py" \
  --metrics-url "http://127.0.0.1:20241/metrics" \
  --systemd-unit "cloudflared.service" \
  --output-path "/var/lib/prometheus/node-exporter/cloudflared-tunnel.prom" \
  --origin "web=http://127.0.0.1:8080/health" \
  --origin "api=http://192.0.2.10:8000/health"
```

To regenerate the configuration and overwrite an existing file:

```bash
python "$TOOL_DIR/configure.py" --overwrite
```

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/cloudflared-tunnel-health"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
cp "$TOOL_DIR/config.example.toml" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
python "$TOOL_DIR/tunnel_health.py" --config "$CONFIG_PATH"
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME "tools-and-scripts/monitoring/cloudflared-tunnel-health"
$ConfigPath = Join-Path $ToolDir "config.local.toml"
Copy-Item (Join-Path $ToolDir "config.example.toml") $ConfigPath
notepad $ConfigPath
py (Join-Path $ToolDir "tunnel_health.py") --config $ConfigPath
```

Review and update every `CUSTOMIZE:` marker.

### Systemd timer scheduling

To schedule the health check every minute, install the script and configuration into system paths, then create a systemd service unit and timer unit:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/cloudflared-tunnel-health"
sudo install -d -m 755 /usr/local/bin /etc/cloudflared-tunnel-health
sudo install -m 755 "$TOOL_DIR/tunnel_health.py" /usr/local/bin/tunnel_health.py
sudo install -m 644 "$TOOL_DIR/config.local.toml" /etc/cloudflared-tunnel-health/config.local.toml
```

`/etc/systemd/system/cloudflared-tunnel-health.service`:

```ini
[Unit]
Description=Cloudflare Tunnel connector and origin health metrics
After=network-online.target

[Service]
Type=oneshot
User=prometheus
ExecStart=/usr/bin/python3 /usr/local/bin/tunnel_health.py --config /etc/cloudflared-tunnel-health/config.local.toml
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/prometheus/node-exporter
```

`/etc/systemd/system/cloudflared-tunnel-health.timer`:

```ini
[Unit]
Description=Collect Cloudflare Tunnel health metrics every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=1s

[Install]
WantedBy=timers.target
```

Enable and start the timer:

```bash
systemctl daemon-reload
systemctl enable --now cloudflared-tunnel-health.timer
```

## Inputs

The TOML configuration contains connector settings, output path, and backend origin probes:

```toml
[tunnel]
# CUSTOMIZE: Set the cloudflared metrics endpoint URL.
metrics_url = "http://127.0.0.1:20241/metrics"
# CUSTOMIZE: Set the systemd unit name for the cloudflared connector service.
systemd_unit = "cloudflared.service"
# CUSTOMIZE: Set the timeout in seconds for HTTP probes and systemctl commands.
timeout_seconds = 5.0

[metrics]
# CUSTOMIZE: Set the destination .prom file path for node_exporter textfile collector.
output_path = "/var/lib/prometheus/node-exporter/cloudflared-tunnel.prom"
# CUSTOMIZE: Set the Prometheus metric family prefix.
metric_prefix = "cloudflared_tunnel"

[[origins]]
# CUSTOMIZE: Short service label for the origin metric.
name = "web"
# CUSTOMIZE: Origin probe URL (status 200 to 399 is up).
url = "http://127.0.0.1:8080/health"
```

Command-line options override configuration values:
- `--config PATH`: Path to local TOML configuration.
- `--metrics-url URL`: Overrides `tunnel.metrics_url`.
- `--systemd-unit UNIT`: Overrides `tunnel.systemd_unit`.
- `--timeout SECONDS`: Overrides `tunnel.timeout_seconds`.
- `--output PATH`: Overrides `metrics.output_path`.
- `--origin NAME=URL`: Replaces the entire configured origin list with the specified check (repeatable; specifying any `--origin` drops unlisted configured checks).
- `--stdout`: Emits metrics to standard output and skips writing to disk.

Precedence order: command-line arguments take precedence over explicit local configuration, which takes precedence over documented defaults.

## Permissions

Run the check as an unprivileged service account (such as `prometheus` or `node_exporter`).
Elevation is not required.

The account requires:
- Read access to `config.local.toml`.
- Write access to the directory containing `output_path`.
- Permission to run `systemctl is-active <unit>`, which systemd permits for unprivileged users.

## Dry run

Use `--stdout` to preview generated Prometheus metrics without writing any files:

```bash
python "$TOOL_DIR/tunnel_health.py" --stdout
```

When `--stdout` is passed, no temporary or permanent file is created on disk.

## Changes made

- When running normally, atomically writes or updates the `.prom` file at `output_path` (default `/var/lib/prometheus/node-exporter/cloudflared-tunnel.prom`).
- Writes use a temporary file in the same directory (`.{name}.{random}.tmp`) and replace the target file using `os.replace`.
- Sets file mode to `0o644` so `node_exporter` can read the metrics.
- `configure.py` creates only `config.local.toml`.

## Safeguard reasoning

- Atomic write: writing to a temporary file before renaming prevents `node_exporter` from scraping partial or corrupted metric files.
- Stopped versus unknown: checking systemd service status distinguishes an intentional service shutdown from a network partition, crashed exporter, or changed local port.
- Negative or NaN validation: invalid metric values returned by an unhealthy endpoint are treated as unknown (-1) rather than parsed as valid counts.
- Label escaping: backend service names have quotes, backslashes, and line feeds escaped to avoid Prometheus format injection.
- Fail-closed origin checks: HTTP probes require a status code between 200 and 399; any other status, timeout, or network exception reports down (0).

## Rollback

To remove exported metrics:
1. Delete the generated `.prom` file from the `node_exporter` textfile directory.
2. If the systemd timer is active, disable and stop it:

```bash
systemctl disable --now cloudflared-tunnel-health.timer
```

3. If installed to system paths, remove `/usr/local/bin/tunnel_health.py` and `/etc/cloudflared-tunnel-health`.
4. Remove `config.local.toml` if local settings are no longer needed.

## Troubleshooting

- `cloudflared_tunnel_ha_connections` is `-1`: the connector service is active in systemd, but `http://127.0.0.1:20241/metrics` did not answer or returned invalid data. Check `cloudflared` logs and verify `--metrics` is enabled on the connector.
- `cloudflared_tunnel_ha_connections` is `0`: the `cloudflared.service` systemd unit is inactive or failed. Run `systemctl status cloudflared.service`.
- `cloudflared_tunnel_origin_up` is `0`: the local backend origin did not answer with status 200-399. Verify the origin service is running and listening on the configured URL.
- `configuration-error: refusing to replace existing file`: remove `config.local.toml` or provide `--overwrite` to `configure.py`.
- Permission denied writing output file: grant write permissions on the textfile directory to the user account running the check.

## Exit behavior

- `0`: Metrics collected and successfully written to destination textfile or printed to stdout.
- `1`: Invalid configuration, runtime error, or failure writing to output file.
- `2`: Invalid command-line arguments (syntax or type errors detected by argparse).
