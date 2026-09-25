# Proxmox subscription notice

Check, patch, or restore the Proxmox web UI subscription notice in `proxmoxlib.js` with an exact-count layout guard and service restart.

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

Proxmox VE and Proxmox Backup Server display a subscription reminder dialog on login when running without an active enterprise repository subscription.

This tool provides a guarded, automated mechanism to inspect (`--check`), suppress (`--apply`), or restore (`--restore`) that dialog by editing the client-side JavaScript helper in `proxmoxlib.js`.

An upstream `proxmox-widget-toolkit` package upgrade replaces `proxmoxlib.js` with the stock package file. When that package upgrades, the dialog returns until this script is rerun.

This tool runs on Linux (Proxmox VE and Proxmox Backup Server).
Tested status: Locally checked; live matrix pending.

## Prerequisites

- Bash 4 or newer.
- Core utilities: `awk`, `sed`, `chmod`, `chown`, `mktemp`, `mv`, and `rm`.
- `systemctl` to restart the web proxy service unless `--no-restart` is selected.
- `dpkg-query` to report the installed package version (optional).
- The target file `/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js` (or a custom path passed to `--file`).

## Guided setup

The configurator runs read-only discovery. It detects the package version, whether `pveproxy` or `proxmox-backup-proxy` is installed, and the current layout of `proxmoxlib.js`. It writes `config.local.conf` with mode `0600` and refuses to overwrite an existing file.

```bash
TOOL_DIR="$HOME/tools-and-scripts/virtualization/proxmox-subscription-notice"
"$TOOL_DIR/configure.sh" --print-discovery
"$TOOL_DIR/configure.sh"
```

## Manual setup

Copy `config.example.conf` to `config.local.conf` and adjust settings:

```bash
TOOL_DIR="$HOME/tools-and-scripts/virtualization/proxmox-subscription-notice"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
cp "$TOOL_DIR/config.example.conf" "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

Run the check to verify the current file:

```bash
"$TOOL_DIR/proxmox-subscription-notice.sh" --config "$CONFIG_PATH" --check
```

## Inputs

`config.local.conf` uses strict `KEY=value` lines. The parser rejects unknown keys and duplicate keys. Values are read literally; shell expansions and command substitutions are not evaluated. Command-line options override configuration file values; configuration file values override defaults.

| Setting | Flag | Default | Purpose |
|---|---|---|---|
| `TOOLKIT_FILE` | `--file PATH` | `/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js` | Absolute path to `proxmoxlib.js` |
| `TOOLKIT_PACKAGE` | `--package NAME` | `proxmox-widget-toolkit` | Package name queried for version reporting |
| `SERVICE_NAME` | `--service NAME` | `pveproxy` | Web proxy service restarted after changes (`pveproxy` or `proxmox-backup-proxy`) |
| `RESTART_SERVICE` | `--restart` / `--no-restart` | `yes` | Whether to restart the web proxy service after a change (`yes` or `no`) |

## Permissions

Running with `--help`, `configure.sh`, or `--check` requires only an ordinary user account.

Applying the patch (`--apply`) or restoring stock checks (`--restore`) requires elevation (`sudo`) because `/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js` is system-owned and `systemctl restart` requires root privileges.

## Dry run

`--check` is the non-destructive preview mode and the default mode:

```bash
TOOL_DIR="$HOME/tools-and-scripts/virtualization/proxmox-subscription-notice"
"$TOOL_DIR/proxmox-subscription-notice.sh" --check
```

It inspects the target file, counts occurrences of stock and patched checks, reports package status, and exits without making changes or restarting any service.

## Changes made

To apply the patch:

```bash
TOOL_DIR="$HOME/tools-and-scripts/virtualization/proxmox-subscription-notice"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
sudo "$TOOL_DIR/proxmox-subscription-notice.sh" --config "$CONFIG_PATH" --apply
```

The script replaces both occurrences of `res.data.status.toLowerCase() !== 'active'` with `res.data.status.toLowerCase() == 'NoMoreNagging'` in `proxmoxlib.js`.

After modifying the file, it restarts the web proxy service (`pveproxy` or `proxmox-backup-proxy`) and verifies that the service is active. If `--no-restart` is configured, the service restart is skipped and the script reminds you to restart the service before browsers load the new file.

## Safeguard reasoning

- Exact-count layout guard: `awk` counts exact occurrences of stock and patched checks. The script requires exactly two stock checks and zero patched checks (for `--apply`), or exactly two patched checks and zero stock checks (for `--restore`). If upstream changes the file layout, every mode refuses to touch the file and exits with code 3.
- Atomic replacement: replacement content is staged in a temporary file created beside the target with `mktemp "$toolkit_file.XXXXXX"`. The staged file is verified against the layout guard, permissions and ownership are copied with `chmod --reference` and `chown --reference`, and `mv -f` replaces the original atomically.
- Cleanup trap: an EXIT trap removes any staged temporary file if writing or verification fails before replacement.
- Post-change verification: the live file on disk is checked immediately after replacement to confirm the desired layout is active.
- Service verification: when restarted, `systemctl is-active --quiet` confirms the service is running.

## Rollback

To restore the original stock subscription checks:

```bash
TOOL_DIR="$HOME/tools-and-scripts/virtualization/proxmox-subscription-notice"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
sudo "$TOOL_DIR/proxmox-subscription-notice.sh" --config "$CONFIG_PATH" --restore
```

The script replaces the patched strings back to stock checks byte-for-byte, verifies the file, and restarts the service.

If manual recovery is needed, reinstalling the upstream package restores the vendor file:

```bash
sudo apt-get install --reinstall proxmox-widget-toolkit
sudo systemctl restart pveproxy
```

## Troubleshooting

- `unsupported subscription-check layout`: upstream modified the toolkit JavaScript structure. Do not force an edit. Inspect `proxmoxlib.js` to see what changed.
- `toolkit file not found`: verify the path in `TOOLKIT_FILE` or pass `--file PATH`.
- `no write access to ...; run with elevation`: `--apply` and `--restore` require `sudo`.
- `file changed and verified, but ... did not restart`: check service logs with `journalctl -u pveproxy` (or configured service).
- Dialog reappears after system updates: a package upgrade of `proxmox-widget-toolkit` replaces `proxmoxlib.js` with the stock package file. Re-run `--apply` after updates.

## Exit behavior

- `0`: check found the patch, or apply/restore finished or had nothing to do.
- `1`: check found the stock layout; the patch is required.
- `2`: the file is missing or unreadable.
- `3`: unsupported layout; nothing changed.
- `4`: the replacement could not be written or failed verification.
- `5`: the file changed and verified, but the service restart failed.
- `64`: invalid argument, invalid configuration, or missing command.
