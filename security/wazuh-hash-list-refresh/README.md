# Wazuh hash list refresh

Rebuild a Wazuh manager CDB list of known-bad SHA-256 hashes from the MalwareBazaar feed, pin the EICAR test hash, and restart the manager service.

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

Wazuh file-integrity monitoring (syscheck) hashes files when they appear or change. Checking file hashes against threat intelligence services like VirusTotal provides detection, but free API tiers have strict request quotas and daily rate limits. When those quotas are reached, queries fail silently without alerting on malicious files.

This tool maintains a local Wazuh Constant Database (CDB) list of known-bad SHA-256 hashes downloaded from the abuse.ch MalwareBazaar recent feed. The list runs entirely locally on the Wazuh manager with zero API quota, zero latency, and zero external dependency at lookup time. The feed captures recent malware samples (deliberately scoped to recent samples to avoid excessive memory consumption in `analysisd`). The tool pins the harmless EICAR test hash at the top to allow deterministic testing of alert pipelines, enforces a minimum-entry gate, installs the file with correct ownership and permissions, and restarts the manager service so `analysisd` compiles the updated CDB table.

Tested status: Locally checked; live matrix pending.

## Prerequisites

- Linux with Bash 4 or newer.
- Core utilities: `curl`, `install`, `mv`, `tr`, `grep`, `sort`, `sed`, `wc`, `mktemp`.
- Wazuh manager installed with list directory support (`/var/ossec/etc/lists`).
- List registered in `/var/ossec/etc/ossec.conf` and referenced by a detection rule (for example, in `/var/ossec/etc/rules/local_rules.xml`). The script only writes the list and restarts the manager; Wazuh requires explicit list registration and rule references before it evaluates entries. See the [Wazuh CDB list documentation](https://documentation.wazuh.com/current/user-manual/ruleset/cdb-list.html).

Minimal list registration in `/var/ossec/etc/ossec.conf` within `<ruleset>`:

```xml
<ruleset>
  <list>etc/lists/known-bad-hashes</list>
</ruleset>
```

Minimal detection rule in `/var/ossec/etc/rules/local_rules.xml`:

```xml
<group name="syscheck,">
  <rule id="100200" level="12">
    <if_group>syscheck</if_group>
    <list field="sha256" lookup="match_key">etc/lists/known-bad-hashes</list>
    <description>File SHA-256 hash matched known-bad MalwareBazaar list ($(sha256))</description>
  </rule>
</group>
```

- systemd managing `wazuh-manager.service` (or a custom restart command).

Safety metadata:

| Field | Value |
|---|---|
| Platform | Linux |
| Privilege | Elevated (`sudo` or root) for list installation and service restart; standard user for `--dry-run`, and standard user for `--output` (skips ownership changes when not running as root or when owner and group are empty in config) |
| State change | Overwrites `/var/ossec/etc/lists/known-bad-hashes` and restarts `wazuh-manager.service` |
| Preview | `--dry-run` downloads and builds in a temporary file without touching the active list or restarting the service |
| Rollback | Restore the previous list copy and restart `wazuh-manager.service` |
| Exit codes | `0` on success or dry run; `1` on invalid input, network failure, feed validation failure, install error, restart failure, or verify failure |

## Guided setup

Run `configure.sh` to inspect local paths and create `config.local.conf`:

```bash
TOOL_DIR="/usr/local/src/tools-and-scripts/linux/wazuh-hash-list-refresh"
"$TOOL_DIR/configure.sh" --print-discovery
"$TOOL_DIR/configure.sh"
```

The configurator inspects whether `/var/ossec/etc/lists` exists, checks for the `wazuh` system user and group, checks for `wazuh-manager.service`, and writes `config.local.conf` with file permissions `0600`. It refuses to replace an existing configuration file without `--overwrite`.

## Manual setup

Copy `config.example.conf` to `config.local.conf` and adjust settings:

```bash
TOOL_DIR="/usr/local/src/tools-and-scripts/linux/wazuh-hash-list-refresh"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
cp "$TOOL_DIR/config.example.conf" "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

Review every `CUSTOMIZE:` marker. Confirm `FEED_URL`, `LIST_PATH`, `OWNER`, `GROUP`, `MODE`, `MIN_ENTRIES`, `CURL_TIMEOUT`, and `RESTART_UNIT`.

To schedule recurring execution with systemd, install the script, configuration, and provided units:

```bash
sudo install -d /etc/wazuh-hash-list-refresh
sudo install -m 600 "$CONFIG_PATH" /etc/wazuh-hash-list-refresh/config.local.conf
sudo install -m 755 "$TOOL_DIR/wazuh-hash-list-refresh.sh" /usr/local/sbin/wazuh-hash-list-refresh.sh
sudo install -m 644 "$TOOL_DIR/systemd/wazuh-hash-list.service" /etc/systemd/system/wazuh-hash-list.service
sudo install -m 644 "$TOOL_DIR/systemd/wazuh-hash-list.timer" /etc/systemd/system/wazuh-hash-list.timer
sudo systemctl daemon-reload
sudo systemctl enable --now wazuh-hash-list.timer
```

## Inputs

Configuration values are read from `config.local.conf` (or the path passed to `--config`). The format is strict shell `KEY=value`. Empty lines and `#` comments are ignored. Shell expansion is not evaluated. Command-line options take precedence over configuration values, and configuration values take precedence over documented defaults.

| Variable | Purpose | Default |
|---|---|---|
| `FEED_URL` | Feed endpoint returning recent SHA-256 hashes | `https://bazaar.abuse.ch/export/txt/sha256/recent/` |
| `LIST_PATH` | Path to the target Wazuh manager CDB list | `/var/ossec/etc/lists/known-bad-hashes` |
| `OWNER` | User owner applied during list installation | `wazuh` |
| `GROUP` | Group owner applied during list installation | `wazuh` |
| `MODE` | File permission mode applied during list installation | `660` |
| `MIN_ENTRIES` | Minimum valid entries required to accept the list | `2` |
| `CURL_TIMEOUT` | Maximum download duration in seconds | `120` |
| `RESTART_UNIT` | systemd service unit restarted after change | `wazuh-manager.service` |
| `RESTART_COMMAND` | Custom command to run instead of `systemctl restart` | Empty |
| `VERIFY_COMMAND` | Optional command to verify service status after `RESTART_COMMAND` | Empty |

Command-line options:
- `--feed-url URL`: Override `FEED_URL`.
- `--list-path PATH`: Override `LIST_PATH`.
- `--owner USER`: Override `OWNER`.
- `--group GROUP`: Override `GROUP`.
- `--mode MODE`: Override `MODE`.
- `--min-entries COUNT`: Override `MIN_ENTRIES`.
- `--curl-timeout SECONDS`: Override `CURL_TIMEOUT`.
- `--restart-unit UNIT`: Override `RESTART_UNIT`.
- `--restart-command CMD`: Override `RESTART_COMMAND`.
- `--verify-command CMD`: Override `VERIFY_COMMAND`.
- `--output PATH, -o PATH`: Write the rebuilt list to `PATH` without restarting the manager.
- `--dry-run`: Download and parse feed into a temporary file, report count, and exit without installing or restarting.
- `--config PATH`: Path to a strict `KEY=value` configuration file.
- `-h, --help`: Display usage summary and exit.

## Permissions

- `--dry-run` and `configure.sh --print-discovery` run with standard user privileges.
- Writing to `--output PATH` works for a standard user by skipping ownership changes when not running as root or when owner and group are empty in config. Write access to the destination path is required.
- Standard execution (`wazuh-hash-list-refresh.sh` without `--output`) requires root privileges (via `sudo` or systemd service) to write to `/var/ossec/etc/lists/` and invoke `systemctl restart wazuh-manager.service`.

## Dry run

Run with `--dry-run` to test feed accessibility and parsing logic without modifying any files or restarting services:

```bash
TOOL_DIR="/usr/local/src/tools-and-scripts/linux/wazuh-hash-list-refresh"
"$TOOL_DIR/wazuh-hash-list-refresh.sh" --dry-run
```

Expected output:

```text
dry run: built 872 entries for target /var/ossec/etc/lists/known-bad-hashes (no changes made)
```

## Changes made

When executed live without `--dry-run`:

1. Creates a temporary file for downloading and formatting feed entries.
2. Pins the standard EICAR test hash (`275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f:eicar-test-file`) as the first line.
3. Fetches recent SHA-256 hashes from `FEED_URL` with `curl`.
4. Sanitizes feed entries: removes carriage returns and quotation marks, filters valid 64-character hexadecimal values, normalizes them to lowercase, excludes duplicate EICAR occurrences, sorts uniquely, and appends the `:malwarebazaar` CDB value.
5. Verifies that the built list contains at least `MIN_ENTRIES` (default `2`). If fewer entries exist, the script terminates immediately with exit status `1` and leaves the active list untouched.
6. Atomically installs the list: writes the finished list to a temporary file in the destination directory with the target owner and mode (skipping ownership changes when not running as root or when owner and group are empty in config), then renames it over the destination path with `mv`.
7. Restarts the service and verifies its state: on the standard path, restarts `wazuh-manager.service` via `systemctl restart` and verifies it is active with `systemctl is-active --quiet`. When a custom `RESTART_COMMAND` is configured, executes that command; `systemctl is-active` is not run on custom restart commands. If an optional `VERIFY_COMMAND` is configured, it executes afterwards and its failure makes the script exit nonzero.
8. Deletes all temporary files via an exit trap.

If `--output PATH` is provided, steps 1 through 6 write to `PATH` (skipping ownership changes when not running as root or when owner and group are empty in config), service restart is skipped, and the exit trap removes temporary files.

## Safeguard reasoning

- Pinned EICAR hash: The standard EICAR antivirus test file hash is pinned permanently at line 1. This guarantees that file-integrity monitoring rules can be tested on demand without downloading or executing real malware.
- Minimum entry gate: If the upstream feed returns an HTTP error, empty body, or garbled response, parsing yields fewer entries than `MIN_ENTRIES`. The script refuses to install the truncated list, preventing the manager's active list from being wiped out.
- Atomic installation: The list is written to a temporary staging file in the target destination directory with target permissions, then atomically renamed over the destination path with `mv`. If any failure occurs before the rename, the existing destination file remains byte-identical.
- Standard user execution: When `--output` is specified, the script allows standard users to write the list without root privileges by skipping ownership adjustments when not running as root or when owner and group are empty in config.
- Service verification boundaries: `systemctl is-active --quiet` runs on the standard systemd restart path. When `RESTART_COMMAND` is configured, the script checks only the command exit code unless an explicit `VERIFY_COMMAND` is configured to verify service health.
- Strict configuration parsing: `load_config` rejects duplicate keys, lower-case keys, and lines missing `=`. Shell expansion is disabled to prevent command injection from untrusted configuration lines.
- Non-overwrite configurator: `configure.sh` refuses to overwrite existing configurations unless `--overwrite` is explicitly provided.

## Rollback

Wazuh compiles CDB lists into `.cdb` binaries upon startup. The script does not automatically archive old lists. To prepare a manual rollback copy before running an update:

```bash
sudo cp /var/ossec/etc/lists/known-bad-hashes /var/ossec/etc/lists/known-bad-hashes.bak
```

If an updated list causes issues or needs to be reverted:

```bash
sudo install -o wazuh -g wazuh -m 660 /var/ossec/etc/lists/known-bad-hashes.bak /var/ossec/etc/lists/known-bad-hashes
sudo systemctl restart wazuh-manager.service
sudo systemctl is-active wazuh-manager.service
```

## Troubleshooting

- `error: failed to download feed from <URL>`: Check outbound internet access, DNS resolution, and firewall rules for connections to `bazaar.abuse.ch`. Verify `curl` works from the command line.
- `error: refusing to install a list with 1 entries`: The feed returned no usable hashes or was empty. The existing list was left untouched. Verify the feed URL and format.
- `error: required command not found: <cmd>`: Ensure standard core utilities (`curl`, `install`, `mv`, `tr`, `grep`, `sort`, `sed`, `wc`) are installed and available on `PATH`.
- `error: failed to restart wazuh-manager.service` or `service is not active after restart`: Check `systemctl status wazuh-manager.service` and review `/var/ossec/logs/ossec.log` for CDB list syntax errors or analysisd startup problems.
- `error: verify command failed: <cmd>`: The command configured in `VERIFY_COMMAND` or `--verify-command` exited nonzero after service restart. Check command syntax and inspect service logs.
- `error: refusing to replace existing file: <path>`: `configure.sh` found an existing configuration file. Edit the file directly or pass `--overwrite` to regenerate it.
- `error: min-entries must be a positive integer`: Verify configuration or command-line argument format.

## Exit behavior

- `0`: List rebuilt and installed successfully and manager restarted; list written to `--output`; or dry run completed successfully.
- `1`: Invalid command-line argument, invalid configuration, feed download failure, feed validation failure (fewer than minimum entries), file installation failure, manager service restart failure, or verification command failure.
