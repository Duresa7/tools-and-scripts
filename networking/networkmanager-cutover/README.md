# NetworkManager ifupdown cutover

`networkmanager-ifupdown-cutover.sh` moves one Debian interface from ifupdown ownership to an existing NetworkManager connection. It installs a prepared replacement for `/etc/network/interfaces`, activates the connection, and checks the exact IPv4 address, default gateway, device state, and DNS resolution.

Run this from a local console, hypervisor console, or another out-of-band path. An incorrect profile or address can interrupt SSH before the rollback finishes.

## What you must customize

Do not edit addresses or interface names into the script. Supply your Linux interface name, existing NetworkManager profile, candidate interfaces file, expected IPv4 address with prefix, and expected gateway through the required flags. If the default DNS probe is unsuitable, pass a hostname your resolver should answer with `--dns-probe`. Run the script with `--help` for the full input list, then use `--dry-run` before `--confirm`.

## Prepare the replacement file

The script does not edit network stanzas. Create a complete candidate file yourself so its final contents can be reviewed before the interface goes down. This example leaves only loopback under ifupdown:

```text
auto lo
iface lo inet loopback
```

Save the candidate outside `/etc/network/interfaces`. `ifquery` verifies that neither the file nor any path included through a `source` directive defines the selected interface.

## Prepare the NetworkManager profile

Create and inspect the profile before cutover. The profile must bind to the interface passed to the script.

```bash
sudo nmcli connection add \
  type ethernet \
  ifname ens18 \
  con-name server-static \
  ipv4.method manual \
  ipv4.addresses 192.0.2.10/24 \
  ipv4.gateway 192.0.2.1 \
  ipv4.dns 192.0.2.53 \
  connection.autoconnect yes

sudo nmcli connection show server-static
```

## Run preflight

```bash
sudo networking/networkmanager-cutover/networkmanager-ifupdown-cutover.sh \
  --interface ens18 \
  --connection server-static \
  --candidate /root/interfaces.networkmanager \
  --expect-ipv4 192.0.2.10/24 \
  --expect-gateway 192.0.2.1 \
  --dns-probe deb.debian.org \
  --dry-run
```

The dry run verifies files, tools, ifupdown ownership, the candidate, the NetworkManager service, and the profile-to-interface binding. It changes no file, interface, connection, or service.

## Run the cutover

Repeat the same command with `--confirm` in place of `--dry-run`. A live run requires root. Use `--skip-dns-check` only when the target has no working resolver by design.

Before the interface goes down, the script creates a timestamped copy such as `/etc/network/interfaces.pre-networkmanager.20260720T120000Z`. A failed command, timeout, interrupt, or termination signal restores that file, brings down the NetworkManager profile, and restarts legacy networking. The backup remains after success for manual rollback.

The script exits `0` only after every requested network assertion passes. A failed preflight or a rolled-back cutover returns a nonzero status.
