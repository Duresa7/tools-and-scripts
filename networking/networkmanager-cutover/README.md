# NetworkManager cutover

Move one Linux interface to an existing NetworkManager profile without editing addresses into the operational script. The command checks one exact IPv4 address, the full default-route set and its attributes, the complete DNS server set, the active profile, and optional name resolution.

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

Use the `ifupdown` path on Debian 13 when `/etc/network/interfaces` owns the selected device. Use `netplan` on Ubuntu 24.04 when one YAML file under `/etc/netplan` owns it. Rocky 10 uses the `networkmanager` path because RHEL 10 removed support for legacy `ifcfg` files; validate an existing NetworkManager keyfile or profile directly. See the [RHEL 10 networking documentation](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html-single/configuring_and_managing_networking/configuring_and_managing_networking).

This is a Linux-only tool. Run a live cutover from a local console, hypervisor console, or another path that survives loss of SSH.

## Prerequisites

- Bash 4 or newer, NetworkManager, `nmcli`, `iproute2`, and systemd.
- ifupdown with `ifquery`, `ifdown`, and `ifup` for the Debian path.
- Netplan for the Ubuntu path.
- An existing NetworkManager connection bound to the selected interface.
- A reviewed replacement file for ifupdown or Netplan. The script does not invent network configuration.

## Guided setup

The configurator reads local interface, route, DNS, profile, and stack information. It does not contact another machine or change network state.

```bash
TOOL_DIR="$HOME/tools-and-scripts/networking/networkmanager-cutover"
"$TOOL_DIR/configure.sh" --print-discovery
"$TOOL_DIR/configure.sh"
```

The second command writes ignored `config.local.conf` with mode `0600`. It refuses to replace an existing file.

## Manual setup

Copy the example and replace every `CUSTOMIZE:` value:

```bash
TOOL_DIR="$HOME/tools-and-scripts/networking/networkmanager-cutover"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
cp "$TOOL_DIR/config.example.conf" "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

For ifupdown, the candidate must omit the selected interface, including definitions reached through `source` directives. For Netplan, the candidate must set `renderer: NetworkManager`, contain the selected interface, and pass `netplan generate` in an isolated temporary root.

Create and inspect the NetworkManager profile before the cutover. Named variables keep the command reusable:

```bash
INTERFACE_NAME="enp1s0"
CONNECTION_NAME="server-static"
IPV4_CIDR="192.0.2.10/24"
GATEWAY_IPV4="192.0.2.1"
DNS_IPV4="192.0.2.53"

sudo nmcli connection add type ethernet ifname "$INTERFACE_NAME" \
  con-name "$CONNECTION_NAME" ipv4.method manual \
  ipv4.addresses "$IPV4_CIDR" ipv4.gateway "$GATEWAY_IPV4" \
  ipv4.dns "$DNS_IPV4" connection.autoconnect yes
nmcli connection show "$CONNECTION_NAME"
```

## Inputs

`config.local.conf` is a strict `KEY=value` file. The parser doesn't execute shell expansion, command substitutions, quotes, or unknown keys. Command-line options override the config file; the config file overrides the 30-second timeout and `example.com` DNS probe defaults.

| Value | Purpose |
|---|---|
| `STACK` | `ifupdown`, `netplan`, or `networkmanager` |
| `INTERFACE` | Device reported by `ip link` |
| `CONNECTION_NAME` | Existing profile reported by `nmcli connection show` |
| `CANDIDATE_FILE` | Replacement file for ifupdown or Netplan |
| `SOURCE_FILE` | Current ifupdown or Netplan file that will be backed up |
| `EXPECT_IPV4` | The only expected global IPv4 address and prefix |
| `EXPECT_GATEWAY` | The only expected default IPv4 gateway |
| `EXPECT_ROUTES` | Exact normalized default routes, including device, protocol, table labels, and metrics; separate multiple routes with `|` |
| `EXPECT_DNS` | Exact DNS server set, comma-separated |
| `DNS_PROBE` | Hostname that must resolve; an empty value skips this one check |
| `TIMEOUT_SECONDS` | Seconds allowed for the expected state to appear |

## Permissions

Guided setup, manual editing, dry run, and post-reboot validation use the ordinary login account. Only the live command needs elevation because it replaces a system network file, applies Netplan, or activates a system connection.

## Dry run

```bash
TOOL_DIR="$HOME/tools-and-scripts/networking/networkmanager-cutover"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
"$TOOL_DIR/networkmanager-cutover.sh" --config "$CONFIG_PATH" --dry-run
```

The dry run checks dependencies, configuration syntax, source ownership, candidate validity, profile binding, and stack-specific requirements. It doesn't replace files, stop an interface, activate a profile, or restart a service.

## Changes made

Run the live operation only with an open console:

```bash
TOOL_DIR="$HOME/tools-and-scripts/networking/networkmanager-cutover"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
sudo "$TOOL_DIR/networkmanager-cutover.sh" --config "$CONFIG_PATH" \
  --confirm CUTOVER_NETWORK_CONNECTION
```

The ifupdown path backs up and replaces its interfaces file, stops the old interface, restarts NetworkManager, and activates the selected profile. The Netplan path backs up and replaces one YAML file, runs `netplan generate`, applies it, and activates the profile. The Rocky 10 path changes no source file; it activates the existing NetworkManager profile.

After a successful reboot, rerun the assertions without elevation:

```bash
"$TOOL_DIR/networkmanager-cutover.sh" --config "$CONFIG_PATH" --validate-only
```

## Safeguard reasoning

The candidate is supplied as a complete file because an automatic partial edit can leave two network systems owning one interface. The exact confirmation phrase prevents an old dry-run command from becoming a live cutover after one flag change. Exact address, full route, DNS, and profile comparisons reject a connected state with the wrong metric, protocol, table, or gateway.

## Rollback

A failed command, failed assertion, timeout, interrupt, or termination signal starts rollback. The ifupdown and Netplan paths restore the timestamped source backup. All paths bring down the selected profile and try to reactivate the prior connection. Backups remain after success for manual recovery.

If rollback reports an error, use the local console. Restore the printed backup path, apply the original network stack, and inspect `nmcli connection show` before reconnecting remotely.

## Troubleshooting

- `candidate still configures`: remove the selected interface from the ifupdown candidate and every sourced file.
- `candidate must set renderer`: add `renderer: NetworkManager` at the correct Netplan level.
- `connection targets`: bind the existing profile to the same interface named in the config.
- `current state does not match`: compare the printed `connection`, `ipv4`, `gateway`, `routes`, and `dns` values with the config.
- `rollback-error`: keep the console session open and restore the printed backup manually.

## Exit behavior

- `0`: dry run passed, live cutover passed every assertion, or validation matched every assertion.
- `1`: invalid input, missing dependency, failed preflight, failed state assertion, or rollback after a cutover failure.
- `130`: interrupted with `SIGINT` after the live change started.
- `143`: terminated with `SIGTERM` after the live change started.
