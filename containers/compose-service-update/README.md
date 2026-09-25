# Compose service update

Update one Docker Compose service container by inspecting its active Compose definition, verifying all files stay within an allowed directory root, pulling the service image, and recreating the container with existing dependencies preserved.

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

When managing Docker containers with Compose, updating a single service often requires knowing the exact Compose file stack (including any override or overlay files) used when the container was launched. Docker Compose records the files it used in the container label `com.docker.compose.project.config_files`.

This tool automates updating a single Compose service in place. It queries the running container's Compose configuration files label, validates that all Compose definitions reside inside an allowed directory root, pulls the latest image for that specific service, and recreates the container with `--no-deps --no-build --pull never`. Other services and containers in the Compose project remain untouched.

This is a Linux-only tool. Tested status: Locally checked; live matrix pending.

## Prerequisites

- Linux with Bash 4 or newer.
- Docker Engine with the Docker Compose v2 plugin (`docker compose`).
- Core utilities: `awk`, `head`, `realpath`, `dirname`.
- A running Compose-managed container with the `com.docker.compose.project.config_files` label populated.

## Guided setup

The configurator inspects local Docker state to detect running Compose containers, project names, service names, and parent directories. It does not contact external hosts, pull images, or change container state.

```bash
TOOL_DIR="$HOME/tools-and-scripts/containers/compose-service-update"
"$TOOL_DIR/configure.sh" --print-discovery
"$TOOL_DIR/configure.sh"
```

The second command prompts for container, project, service, and root directory, then writes `config.local.conf` with mode `0600`. It refuses to replace an existing configuration file.

## Manual setup

Copy the example configuration and adjust the values:

```bash
TOOL_DIR="$HOME/tools-and-scripts/containers/compose-service-update"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
cp "$TOOL_DIR/config.example.conf" "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

Review every `CUSTOMIZE:` marker. Set `CONTAINER_NAME`, `PROJECT_NAME`, `SERVICE_NAME`, and `ALLOWED_COMPOSE_ROOT`.

## Inputs

`config.local.conf` is a strict `KEY=value` file. Lines with `#` comments and empty lines are ignored. Shell expansion and command substitutions are not evaluated. Command-line options override config file values.

| Value | Purpose |
|---|---|
| `CONTAINER_NAME` | Name of the running container to inspect for Compose configuration files |
| `PROJECT_NAME` | Docker Compose project name passed to `--project-name` |
| `SERVICE_NAME` | Compose service name to pull and recreate |
| `ALLOWED_COMPOSE_ROOT` | Absolute path to the directory root containing all allowed Compose files |

Command-line options:

- `--container NAME`: Override `CONTAINER_NAME`.
- `--project NAME`: Override `PROJECT_NAME`.
- `--service NAME`: Override `SERVICE_NAME`.
- `--allowed-root PATH`: Override `ALLOWED_COMPOSE_ROOT`.
- `--config PATH`: Strict `KEY=value` configuration file path.
- `--dry-run`: Preview resolved files and exact commands without executing.
- `-h, --help`: Show usage and exit.

## Permissions

Guided setup and manual editing require only standard user privileges.

Inspecting containers, pulling images, and recreating containers require access to the Docker daemon socket (`/var/run/docker.sock`), typically granted by membership in the `docker` group or run via `sudo`.

## Dry run

Dry run is the first operational step before running an update. It queries Docker for the container's Compose files, validates that every file exists under the allowed root, and prints the exact commands without pulling images or recreating containers:

```bash
TOOL_DIR="$HOME/tools-and-scripts/containers/compose-service-update"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
"$TOOL_DIR/compose-service-update.sh" --config "$CONFIG_PATH" --dry-run
```

Expected output:

```text
Resolved Compose files:
  /opt/docker/web/compose.yaml
Planned commands:
  docker compose --project-name web -f /opt/docker/web/compose.yaml pull web
  docker compose --project-name web -f /opt/docker/web/compose.yaml up -d --no-deps --no-build --pull never web
```

## Changes made

Running without `--dry-run` executes the live update:

```bash
TOOL_DIR="$HOME/tools-and-scripts/containers/compose-service-update"
CONFIG_PATH="$TOOL_DIR/config.local.conf"
"$TOOL_DIR/compose-service-update.sh" --config "$CONFIG_PATH"
```

The script performs these operations in order:

1. Queries the target container for `com.docker.compose.project.config_files`.
2. Validates that every referenced Compose file is an absolute path, exists on disk, and resides inside `ALLOWED_COMPOSE_ROOT`.
3. Runs `docker compose --project-name <project> -f <file...> pull <service>` to fetch the newest image for the targeted service.
4. Runs `docker compose --project-name <project> -f <file...> up -d --no-deps --no-build --pull never <service>` to recreate only the targeted service container with the newly pulled image, preserving unmanaged dependencies and preventing unwanted image rebuilding or pulling.

## Safeguard reasoning

- Label-driven file discovery: Docker Compose stacks can include override files. Reading the active container's label ensures updates use the exact files that define the running container.
- Root confinement: Arbitrary container labels could point to arbitrary filesystem paths. Requiring all Compose files to reside under an explicitly approved root path prevents directory traversal or loading untrusted Compose definitions. Only the files directly listed in the container's `com.docker.compose.project.config_files` label are checked; referenced includes or other files loaded by those definitions are not inspected.
- Canonical path checks: Symbolic links and parent directory references (`..`) are resolved and checked against the allowed root. The canonical path check is mandatory: if `realpath` is missing from `PATH` or path resolution fails for the allowed root or any Compose file, execution is refused.
- Targeted execution: Passing `--no-deps` prevents Docker Compose from recreating or restarting dependent containers.
- Offline rebuild prevention: The `--no-build` and `--pull never` flags during `up` guarantee that recreation uses only the image pulled in the preceding step and does not trigger unexpected builds.
- Fail-fast pull gate: If image pulling fails (for example due to network loss or authentication issues), the script terminates immediately without touching or stopping the running container.

## Rollback

If the updated container fails to start or encounters runtime errors after recreation:

1. Docker preserves the previously used image tag in local image storage unless explicitly pruned. Inspect available image tags with `docker images <image-name>`.
2. To revert to the previous container image, specify the previous image tag or digest in the service's Compose definition file or environment variable.
3. Rerun the recreate command manually:

```bash
docker compose --project-name web -f /opt/docker/web/compose.yaml up -d --no-deps --no-build --pull never web
```

4. If the Compose definition itself was modified, restore the previous Compose definition file and rerun the recreate command.

## Troubleshooting

- `container has no Compose configuration files label`: The container was not started with Docker Compose or the label was stripped. Check `docker inspect <container>`.
- `Compose file is outside allowed root`: The container references a Compose file located outside `ALLOWED_COMPOSE_ROOT`. Verify the allowed root in configuration.
- `Compose file does not exist`: A file listed in the container's label has been deleted or moved. Check the path reported in the error message.
- `required command not found: realpath`: `realpath` is not installed or not available on `PATH`.
- `failed to resolve canonical path`: Canonical path resolution failed for the allowed root or a Compose file. Verify the paths exist and permissions allow resolution.
- `failed to inspect container`: Docker daemon is unreachable or the container name does not exist. Check `docker ps` and daemon status.
- `docker compose pull failed`: Network connectivity, registry authentication, or image tag problem. The running container was not stopped.

## Exit behavior

- `0`: Dry run succeeded or live service update completed successfully.
- `1`: Invalid arguments, missing required input, missing dependency (`docker` or `realpath`), container inspection failure, Compose file outside allowed root, missing Compose file, or canonical path resolution failure.
- Nonzero: A failed `docker compose pull` or `docker compose up` command exits with Docker's own exit status.
