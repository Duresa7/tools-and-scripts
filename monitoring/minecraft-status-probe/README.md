# Minecraft status probe

Query a Minecraft Java Edition server using the Server List Ping protocol and report health, version, player counts, MOTD, and latency.

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

Use this to verify Minecraft Java Edition server availability, track active player counts, monitor handshake latency, and validate server responses after upgrades, reboots, or network changes.

The tool runs on Linux, macOS, and Windows with Python 3.11 or newer. It implements the native Minecraft Server List Ping protocol over TCP, supports protocol version configuration, optional ping/pong latency measurement, DNS SRV record resolution, and outputs in plain text or structured JSON.

Tested status: Locally checked; live matrix pending.

## Prerequisites

- Python 3.11 or newer.
- Network access to the Minecraft server TCP port (default 25565).
- Optional `dig` utility if resolving SRV records via `--srv`.

## Guided setup

The configurator writes ignored `config.local.toml` and refuses to replace an existing file. It makes no network calls during configuration.

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/minecraft-status-probe"
python "$TOOL_DIR/configure.py"
```

To configure specific server parameters:

```bash
python "$TOOL_DIR/configure.py" --host "mc.example.net" --port 25565 --format text
```

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/minecraft-status-probe"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
cp "$TOOL_DIR/config.example.toml" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
python "$TOOL_DIR/minecraft_status_probe.py" --config "$CONFIG_PATH"
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/monitoring/minecraft-status-probe'
$ConfigPath = Join-Path $ToolDir 'config.local.toml'
Copy-Item (Join-Path $ToolDir 'config.example.toml') $ConfigPath
notepad $ConfigPath
py (Join-Path $ToolDir 'minecraft_status_probe.py') --config $ConfigPath
```

Review every `CUSTOMIZE:` marker and adjust host, port, timeout, and format settings for your target server.

## Inputs

The TOML configuration file sets the target endpoint and connection options. Precedence resolves from command line arguments first, then `config.local.toml` (or an explicit `--config` path), then built-in defaults.

| Input | Flag | Default | Description |
|---|---|---|---|
| `host` | `host` or `--host` | `127.0.0.1` | Target server hostname or IP address |
| `port` | `--port` | `25565` | Target TCP port (1-65535) |
| `timeout_seconds` | `--timeout` | `10.0` | Connection and read timeout in seconds |
| `protocol_version` | `--protocol-version` | `767` | Protocol version sent in handshake (767 is 1.21/1.21.1) |
| `format` | `--format`, `--json`, `--text` | `text` | Output format: `text` or `json` |
| `srv` | `--srv` / `--no-srv` | `false` | Resolve `_minecraft._tcp.<host>` SRV record; incompatible with `--port` |
| `ping` | `--ping` / `--no-ping` | `false` | Exchange ping/pong packets to measure round-trip latency |

Any explicit `--port` is rejected when SRV is enabled, including when enabled through configuration. Use `--no-srv` to override configuration and allow an explicit port.

Positional host and flags override local configuration:

```bash
python "$TOOL_DIR/minecraft_status_probe.py" mc.example.net --port 25565 --format json
```

## Permissions

Run the probe as an ordinary user. No elevated privileges or administrative permissions are required. The probe only needs read access to its configuration file and network access to reach the server TCP port.

## Dry run

The probe is strictly read-only and does not modify server state. To test execution without querying a live game server, run the test suite against the local fake TCP server:

```bash
pytest monitoring/minecraft-status-probe/tests
```

## Changes made

The probe modifies no files and creates no remote objects. The configurator creates only the designated `config.local.toml` file. It refuses to overwrite that file if it already exists.

## Safeguard reasoning

The probe only performs the Server List Ping handshake; it does not log in, authenticate, or join the game world. Exclusive file creation (`x` mode) prevents the configurator from overwriting existing configuration files during setup. Timeouts and an overall deadline bound the complete exchange so slow or unresponsive hosts cannot hang indefinitely. Outer frame length validation and length caps (maximum 64 KiB status JSON, VarInts capped at 5 bytes) reject malformed packets and prevent excessive memory allocation.

## Rollback

No rollback is required for the probe because it performs no write operations. To remove local configuration, delete `config.local.toml`. The tracked `config.example.toml` template remains intact.

## Troubleshooting

- `error: connection failure: connection timed out`: check network reachability and verify that firewall rules allow traffic to the Minecraft TCP port.
- `error: connection failure: failed to connect`: verify that the Minecraft server process is running and listening on the expected IP and port.
- `error: connection failure: dig command not found`: install `dig` to use SRV record resolution or disable `--srv`.
- `error: protocol failure: unexpected response packet id`: the remote service answered but did not return a valid Minecraft status response.
- `error: protocol failure: VarInt exceeds five bytes`: the remote endpoint sent non-protocol or corrupted binary data.
- `error: protocol failure: response frame length mismatch`: the server sent a packet whose outer frame length did not match its contents.
- `error: connection failure: no Minecraft SRV record found`: verify DNS resolution and confirm that an SRV record exists for `_minecraft._tcp.<host>`.

## Exit behavior

- `0`: Success. The Minecraft server answered with a valid status response.
- `1`: Configuration error (invalid port range, mutually exclusive `--srv` and `--port`, or unreadable configuration file).
- `2`: Invalid command-line arguments (argparse syntax/type errors), or connection/network failure (connection refused, host unreachable, timeout, missing `dig`, or SRV lookup failure).
- `3`: Protocol or response failure (unexpected packet ID, frame length mismatch, payload exceeding size limit, corrupted VarInt, early disconnect, or malformed JSON).
