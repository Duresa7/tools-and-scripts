# DNF updates textfile collector

Export pending DNF package update counts, security update counts, and kernel reboot requirements into a Prometheus node_exporter textfile collector.

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

Debian-based distributions often use `apt_info.py` from `prometheus-node-exporter-collectors` to export `apt_upgrades_pending` and `node_reboot_required`. DNF-based distributions such as Fedora, Rocky Linux, AlmaLinux, RHEL, and CentOS Stream lack an equivalent package out of the box.

This tool fills that gap by checking package status and exporting four Prometheus metrics:
- Pending update counts grouped by repository (`dnf_upgrades_pending{repo="..."}`)
- Total pending security updates (`dnf_security_upgrades_pending`)
- Whether the running kernel is older than the newest installed kernel (`node_reboot_required`)
- Check execution timestamp (`dnf_updates_check_timestamp_seconds`)

This is a Linux-only tool. Tested status: Locally checked; live matrix pending.

## Prerequisites

- Linux with Bash 4 or newer.
- DNF (`dnf`), `rpm`, and core utilities (`awk`, `chmod`, `date`, `grep`, `mktemp`, `mv`, `rm`, `sort`, `tail`, `uname`).
- `node_exporter` installed and configured with `--collector.textfile.directory`.
- systemd for scheduled periodic execution.

## Guided setup

The configurator inspects local processes to detect the node_exporter textfile directory, checks the running kernel, and checks the newest installed kernel. It does not contact external hosts, run package managers, or modify existing files.

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/dnf-updates"
"$TOOL_DIR/configure.sh" --print-discovery
"$TOOL_DIR/configure.sh"
```

The second command prompts for the textfile path and kernel package, then writes `config.local.conf` with mode `0600`. It refuses to replace an existing configuration file.

## Manual setup

Copy the example configuration and adjust the values:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/dnf-updates"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
cp "$TOOL_DIR/config.example.conf" "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

Review every `CUSTOMIZE:` marker.

Install the collector script and systemd units:

```bash
sudo cp "$TOOL_DIR/dnf-updates-textfile.sh" /usr/local/sbin/dnf-updates-textfile
sudo chmod 0755 /usr/local/sbin/dnf-updates-textfile
sudo cp "$CONFIG_PATH" /usr/local/etc/dnf-updates-textfile.conf
sudo chmod 0644 /usr/local/etc/dnf-updates-textfile.conf

sudo cp "$TOOL_DIR/systemd/dnf-updates-textfile.service" /etc/systemd/system/
sudo cp "$TOOL_DIR/systemd/dnf-updates-textfile.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dnf-updates-textfile.timer
```

To run an initial check immediately rather than waiting for the timer:

```bash
sudo systemctl start dnf-updates-textfile.service
```

If node_exporter does not yet enable the textfile collector, add a systemd drop-in:

```bash
sudo mkdir -p /etc/systemd/system/node_exporter.service.d
sudo tee /etc/systemd/system/node_exporter.service.d/textfile.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/bin/node_exporter --collector.textfile.directory=/var/lib/prometheus/node-exporter
EOF
sudo systemctl daemon-reload
sudo systemctl restart node_exporter
```

## Inputs

`config.local.conf` is a strict `KEY=value` file. Lines with `#` comments and empty lines are ignored. Shell expansion and command substitutions are not evaluated.

| Value | Purpose | Default |
|---|---|---|
| `OUTPUT_FILE` | Target textfile path; must be absolute and end in `.prom` | `/var/lib/prometheus/node-exporter/dnf.prom` |
| `KERNEL_PACKAGE` | Package name providing the kernel to query with `rpm` | `kernel` |

Resolution precedence: command line (`--output`, `--kernel-package`), then explicit configuration file (`--config`), then documented defaults.

## Permissions

Discovery (`configure.sh --print-discovery`) and dry run (`dnf-updates-textfile.sh --dry-run`) run as an ordinary user without elevation.

Live collection writes to the textfile directory (often `/var/lib/prometheus/node-exporter/`), requiring write permission to that directory. Run the live command with `sudo` or via the systemd service.

Installing files into `/usr/local/sbin/`, `/usr/local/etc/`, and `/etc/systemd/system/` requires administrative elevation.

## Dry run

Run the collector with `--dry-run` to inspect generated metrics on stdout without writing the collector's metrics file:

```bash
TOOL_DIR="$HOME/tools-and-scripts/monitoring/dnf-updates"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
"$TOOL_DIR/dnf-updates-textfile.sh" --config "$CONFIG_PATH" --dry-run
```

The dry run queries `dnf` and `rpm`, prints the resulting Prometheus metrics to stdout, and logs a summary line to stderr. DNF still runs and may touch its own cache and logs; only the collector's metrics file is not written.

## Changes made

The live collector stages metrics in a temporary file beside the destination (`dnf.prom.XXXXXX`), sets mode `0644`, and renames it over the target path (`mv -f`).

The output file contains four metrics:
- `dnf_upgrades_pending{repo="<repo>"}`: pending package upgrade count for each repository with pending updates (repositories with zero updates emit no sample).
- `dnf_security_upgrades_pending`: count of pending upgrades carrying a security advisory.
- `node_reboot_required`: `1` when the running kernel does not match the newest installed kernel package, `0` otherwise.
- `dnf_updates_check_timestamp_seconds`: Unix timestamp of the check run.

The configurator writes only `config.local.conf` with mode `0600`.

## Safeguard reasoning

`dnf check-update` exits `100` when updates are available and `0` when the system is clean. Any other exit code indicates network errors, repository issues, or metadata corruption. When an unexpected exit code occurs, the script aborts immediately with exit code 2 and leaves the existing metrics file untouched. node_exporter continues serving the prior metrics, and Prometheus alerting can detect staleness via `dnf_updates_check_timestamp_seconds`.

Writing to a temporary file in the same directory and replacing the destination with `mv -f` ensures atomic updates. node_exporter never reads a partially written file.

The script rejects any output path that is not an absolute path ending in `.prom`. If an existing non-empty file lacks the `# TYPE dnf_updates_check_timestamp_seconds gauge` header, the script refuses to replace it. This stops a typo from overwriting an unrelated system file or another collector's metrics.

The parser filters out `Obsoleting Packages` lines from `dnf check-update` output to avoid inflating upgrade counts with replacement notices.

## Rollback

The collector changes no packages, repositories, or system configuration.

To remove the collector and stop exporting metrics:

```bash
sudo systemctl disable --now dnf-updates-textfile.timer
sudo rm -f /etc/systemd/system/dnf-updates-textfile.service
sudo rm -f /etc/systemd/system/dnf-updates-textfile.timer
sudo rm -f /usr/local/sbin/dnf-updates-textfile
sudo rm -f /usr/local/etc/dnf-updates-textfile.conf
sudo rm -f /var/lib/prometheus/node-exporter/dnf.prom
sudo systemctl daemon-reload
```

Deleting `config.local.conf` removes local configuration; the tracked `config.example.conf` remains unchanged.

## Troubleshooting

- `refusing to replace ... because it wasn't written by this tool`: the destination file already exists and does not contain the expected timestamp metric header. Remove the file manually or choose a distinct `.prom` filename.
- `output directory not found`: create the directory named by node_exporter's `--collector.textfile.directory` before starting the service.
- `no write access to ...`: run the command with `sudo` or grant the executing account write permissions to the textfile directory.
- `dnf check-update exited with status ...`: verify repository mirrors and internet connectivity. The previous metrics file was left unchanged.
- `node_textfile_scrape_error 1`: verify the textfile has mode `0644` and that the directory is readable by the user running node_exporter.

## Exit behavior

- `0`: textfile written successfully, or dry-run metrics printed.
- `1`: invalid input, missing required command, or unsafe output path.
- `2`: `dnf check-update` failed; the existing textfile was left unchanged.
- `3`: textfile could not be staged, written, or replaced; the existing textfile was left unchanged.
