# TeamSpeak channel migration

Export a TeamSpeak 3 channel hierarchy through the desktop client's ClientQuery plugin and recreate it through a target ServerQuery endpoint. The source can remain inaccessible through ServerQuery as long as an authorized user can connect with the desktop client.

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

The export includes names, hierarchy, topics, descriptions, codecs, limits, flags, phonetic names, and banner settings. TeamSpeak doesn't expose channel passwords in a reversible form, so they aren't copied. Custom icon files require a separate migration.

The Python 3.11 tool runs on Linux, macOS, and Windows. Export uses ClientQuery. Import uses the plain TCP ServerQuery protocol.

## Prerequisites

- TeamSpeak 3 desktop client with ClientQuery enabled for export.
- An open source-server tab in that client.
- A target ServerQuery account allowed to list and create channels.
- Python 3.11 or newer and TCP reachability to each configured endpoint.
- A disposable target for the first live import.

## Guided setup

The configurator writes ignored `config.local.toml` and refuses to replace it. Without `--discover-source`, it makes no network connection.

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/migrations/teamspeak-channel-migration"
python "$TOOL_DIR/configure.py"
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/migrations/teamspeak-channel-migration'
py (Join-Path $ToolDir 'configure.py')
```

To list open ClientQuery tabs and record the selected handler, give explicit consent and provide the API key through the configured environment variable or hidden prompt:

```bash
python "$TOOL_DIR/configure.py" --discover-source
```

The discovery session authenticates, lists handlers, selects one tab, and reads its server name. It doesn't export channels or change either server.

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/migrations/teamspeak-channel-migration"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
cp "$TOOL_DIR/config.example.toml" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

PowerShell:

```powershell
$ConfigPath = Join-Path $ToolDir 'config.local.toml'
Copy-Item (Join-Path $ToolDir 'config.example.toml') $ConfigPath
notepad $ConfigPath
```

Replace every `CUSTOMIZE:` value. Relative input and output paths are resolved from the TOML file's directory.

## Inputs

`[source]` defines the ClientQuery host, port, handler ID, timeout, API-key environment-variable name, and JSON output path. `[target]` defines the ServerQuery host, port, virtual server ID, query username, timeout, password environment-variable name, and JSON input path.

CLI values override TOML values. TOML values override the local ClientQuery endpoint `127.0.0.1:25639`, target port `10011`, virtual server ID `1`, 10-second timeouts, and `channels.json` paths. A live import has no default query username; supply an account created for the migration.

Credentials never appear in TOML or command arguments. Bash:

```bash
read -r -s -p 'ClientQuery API key: ' TS3_CLIENTQUERY_API_KEY
export TS3_CLIENTQUERY_API_KEY
python "$TOOL_DIR/teamspeak_channels.py" --config "$CONFIG_PATH" export
unset TS3_CLIENTQUERY_API_KEY
```

PowerShell:

```powershell
$env:TS3_CLIENTQUERY_API_KEY = Read-Host 'ClientQuery API key' -MaskInput
py (Join-Path $ToolDir 'teamspeak_channels.py') --config $ConfigPath export
Remove-Item Env:TS3_CLIENTQUERY_API_KEY
```

## Permissions

Run as an ordinary user. ClientQuery and ServerQuery permissions determine what can be read or created. Use a target query account limited to the target virtual server and channel operations required by the migration.

## Dry run

Dry run reads the export, validates every channel ID and parent reference, orders parents before children, and prints planned creation without contacting the target:

```bash
python "$TOOL_DIR/teamspeak_channels.py" --config "$CONFIG_PATH" import --dry-run
```

`--skip-existing` needs a live target query, so it can't be combined with dry run.

## Changes made

Export writes one JSON file through a temporary file and atomic installation. It refuses an existing path unless `--force` is present. Import creates permanent or semi-permanent channels represented in the export. It doesn't delete or edit existing channels.

Live import example:

```bash
read -r -s -p 'ServerQuery password: ' TS3_SERVERQUERY_PASSWORD
export TS3_SERVERQUERY_PASSWORD
python "$TOOL_DIR/teamspeak_channels.py" --config "$CONFIG_PATH" import --skip-existing
unset TS3_SERVERQUERY_PASSWORD
```

## Safeguard reasoning

Parents are created before children. If a parent fails, its child fails instead of being moved to the root. `--skip-existing` maps a same-name channel under the same parent to its existing ID so descendants retain their hierarchy. Sensitive query failures omit the command and server-supplied extra text that could echo a credential.

Review the JSON before sharing it. Channel names, topics, and descriptions can contain private information.

## Rollback

Export rollback is deleting the local JSON file. Import has no automatic delete because a partial run may have reused existing channels. Keep the command output, inspect `created:` rows, and remove only channels created by that run through an approved TeamSpeak administration path.

Run the first import against a disposable virtual server. A second run with `--skip-existing` can reuse successfully created parents after you correct the failure.

## Troubleshooting

- `no server tabs are open`: connect the desktop client to the source and retry.
- `handler ID ... is not open`: rerun configurator discovery or set `handler_id = 0`.
- `target query username is required`: set `target.query_username` or pass `--username`.
- `depends on a parent that was not created`: correct the parent failure before rerunning.
- `output already exists`: choose a new path; use `--force` only after preserving the prior export.

## Exit behavior

- `0`: export completed, dry run validated every channel, or live import had no failed channel.
- `1`: invalid config or JSON, missing credential, connection failure, query failure, or filesystem error.
- `2`: import completed but one or more channels failed.
