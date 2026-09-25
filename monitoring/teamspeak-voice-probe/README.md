# TeamSpeak voice probe

Probe TeamSpeak 3 voice endpoints with a real UDP Init1 handshake and export Prometheus textfile metrics distinguishing server outages from relay or DNS faults.

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

Standard TCP checks or generic UDP pingers cannot verify that a TeamSpeak 3 voice service is functioning. An open UDP port or an ICMP port unreachable response proves nothing about daemon health.

This tool sends a real TeamSpeak 3 Init1 step-0 packet. Only a datagram matching the documented 32-byte Init1 step-1 reply layout (MAC `TS3INIT1`, packet ID 101, packet type `0x88`, step byte `0x01`, 16 bytes of server data, and echoing the client's 4 random nonce bytes in reversed order) counts as a healthy reply.

Each configured server is probed both locally on its host UDP port and publicly via its `_ts3._udp` SRV record. Comparing both probes isolates faults automatically:
- Local up with public down indicates a tunnel, reverse proxy, relay, or DNS failure.
- Local down indicates the voice daemon itself is stopped or unresponsive.

An optional ServerQuery check can log in and record online client counts, channel totals, uptime, and slot limits.

Platform: Linux, macOS, and Windows with Python 3.11 or newer.
Tested status: Locally checked; live matrix pending.

## Prerequisites

- Python 3.11 or newer.
- `dig` on `PATH` for SRV record resolution (from `bind9-dnsutils`, `dnsutils`, or `bind-utils`).
- Network access: outbound UDP to voice ports, and optional TCP access to ServerQuery ports.
- Write access to the target textfile collector directory when writing Prometheus metrics.
- Named environment variable holding the ServerQuery password when ServerQuery metrics are enabled.

## Guided setup

The configurator writes ignored `config.local.toml` and refuses to overwrite an existing file.

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/teamspeak-voice-probe"
python "$TOOL_DIR/configure.py"
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/monitoring/teamspeak-voice-probe'
py (Join-Path $ToolDir 'configure.py')
```

To configure specific servers during setup:

```bash
python "$TOOL_DIR/configure.py" \
  --server voice1 voice1.example.com 9987 \
  --server voice2 voice2.example.com 9988
```

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/teamspeak-voice-probe"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
cp "$TOOL_DIR/config.example.toml" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
python "$TOOL_DIR/teamspeak_probe.py" --config "$CONFIG_PATH" check
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/monitoring/teamspeak-voice-probe'
$ConfigPath = Join-Path $ToolDir 'config.local.toml'
Copy-Item (Join-Path $ToolDir 'config.example.toml') $ConfigPath
notepad $ConfigPath
py (Join-Path $ToolDir 'teamspeak_probe.py') --config $ConfigPath check
```

Replace every `CUSTOMIZE:` value in `config.local.toml`.

## Inputs

The TOML configuration contains three tables:
- `[probe]`: `local_host` (defaults to `127.0.0.1`), `timeout_seconds` (defaults to `4.0`), and optional `dns_server`.
- `[metrics]`: `output_path` ending with `.prom`.
- `[[servers]]`: repeatable table with `name`, public `address`, `local_port`, optional `query_port`, `query_username`, `query_password_env`, and `virtual_server_id`.

CLI flags (`--local-host`, `--timeout`, `--dns-server`, `--output`) override TOML settings. TOML settings override built-in defaults.

Ad-hoc voice checks can run without a configuration file:

```bash
python "$TOOL_DIR/teamspeak_probe.py" check --public voice1.example.com --local 9987
```

Passwords are read strictly from the environment variable named in `query_password_env`. Never store passwords in the TOML file or command arguments.

Bash:

```bash
read -r -s -p 'ServerQuery password: ' TS_QUERY_PASSWORD
export TS_QUERY_PASSWORD
python "$TOOL_DIR/teamspeak_probe.py" --config "$CONFIG_PATH" metrics --stdout
unset TS_QUERY_PASSWORD
```

PowerShell:

```powershell
$env:TS_QUERY_PASSWORD = Read-Host 'ServerQuery password' -MaskInput
py (Join-Path $ToolDir 'teamspeak_probe.py') --config $ConfigPath metrics --stdout
Remove-Item Env:TS_QUERY_PASSWORD
```

Periodic collection with systemd:

Unit file `/etc/systemd/system/teamspeak-probe.service`:

```ini
[Unit]
Description=TeamSpeak voice probe textfile exporter
After=network.target

[Service]
Type=oneshot
User=node_exporter
ExecStart=/usr/bin/python3 /opt/tools-and-scripts/monitoring/teamspeak-voice-probe/teamspeak_probe.py --config /opt/tools-and-scripts/monitoring/teamspeak-voice-probe/config.local.toml metrics
```

Timer file `/etc/systemd/system/teamspeak-probe.timer`:

```ini
[Unit]
Description=Run TeamSpeak voice probe every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=teamspeak-probe.service

[Install]
WantedBy=timers.target
```

Periodic collection with cron:

```cron
* * * * * node_exporter /usr/bin/python3 /opt/tools-and-scripts/monitoring/teamspeak-voice-probe/teamspeak_probe.py --config /opt/tools-and-scripts/monitoring/teamspeak-voice-probe/config.local.toml metrics >/dev/null 2>&1
```

## Permissions

Run as an ordinary user, such as `node_exporter` or an unprivileged automation account. No root or administrator privilege is required. The account needs read access to `config.local.toml` and write access to the `.prom` output directory.

## Dry run

Probing is non-destructive. To verify configuration and endpoint connectivity without writing files, run `check`:

```bash
python "$TOOL_DIR/teamspeak_probe.py" --config "$CONFIG_PATH" check
```

To preview rendered Prometheus metrics on standard output without writing to disk:

```bash
python "$TOOL_DIR/teamspeak_probe.py" --config "$CONFIG_PATH" metrics --stdout
```

## Changes made

The `check` command changes no local or remote state.

The `metrics` command writes the target `.prom` file atomically:
1. It writes metrics to a temporary file in the same directory (`.teamspeak.prom.<id>.tmp`).
2. It flushes and fsyncs the contents.
3. It sets file permissions to `0644`.
4. It atomically replaces the target file via `os.replace`.

The configurator writes `config.local.toml` only if the file does not already exist.

## Safeguard reasoning

- Connected UDP socket: The probe calls `connect()` on the UDP socket before sending the handshake packet. The kernel discards datagrams received from any sender other than the probed host and port.
- Exact Init1 reply verification: Only packets matching the documented 32-byte step-1 reply (MAC `TS3INIT1`, packet ID 101, type `0x88`, step byte `0x01`, and client nonce reversed) count as an answering server. Any unexpected reply, echo, or timeout is marked as down.
- Atomic textfile write: `node_exporter` reads `.prom` files concurrently. Atomic replacement prevents partial reads of half-written metrics.
- Credential isolation: Passwords are read only from the named environment variable at runtime. Failed queries do not echo command strings containing passwords.

## Rollback

Because probing is read-only, no service rollback is needed.

To remove written metrics:

```bash
rm -f /var/lib/node_exporter/textfile_collector/teamspeak.prom
```

To remove local configuration:

```bash
rm -f "$TOOL_DIR/config.local.toml"
```

The tracked `config.example.toml` remains unchanged.

## Troubleshooting

- `input-error: dig was not found on PATH`: Install your distribution DNS utilities package (`bind9-dnsutils` or `bind-utils`).
- `input-error: metrics output must end in .prom`: Ensure `output_path` ends with `.prom` so `node_exporter` reads it.
- `input-error: metrics output directory does not exist`: Create the parent directory for your `.prom` file before running `metrics`.
- `environment variable '...' is empty or unset`: Set the environment variable holding the ServerQuery password, or set `query_port = 0` to disable ServerQuery metrics.
- `verdict tunnel-or-dns-fault`: The local voice port responded, but the public SRV target did not answer. Verify your SRV record, external IP, firewall, or relay tunnel.
- `verdict server-fault`: The local voice port did not answer. Check whether the TeamSpeak server process is running and bound to the configured UDP port.

## Exit behavior

- `0`: All probed endpoints answered successfully in `check` mode, or metrics were written successfully in `metrics` mode. An empty or unset ServerQuery password environment variable logs a warning to stderr and exports `teamspeak_query_up=0`, but still exits 0 when metric output succeeds; `check` mode probes voice endpoints exclusively and never reads ServerQuery passwords.
- `1`: Invalid configuration file, missing `dig`, invalid metrics output path, or file write failure.
- `2`: Bad arguments or command-line syntax errors (`argparse`), or one or more voice endpoints failed to answer in `check` mode.
