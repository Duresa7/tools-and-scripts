# Git preview server

Serve non-dotfile paths tracked by a git working tree over local HTTP for browser preview without exposing untracked files.

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

Viewing local HTML, SVG diagrams, and documentation in a browser often fails when loading `file://` URLs because browsers enforce strict security restrictions on local file navigation, cross-origin assets, and automatic reloads.

This tool runs a lightweight HTTP preview server for a local git working tree. It enforces two primary security boundaries:
1. It listens on loopback (`127.0.0.1`) by default, so network peers cannot connect.
2. It serves only non-dotfile paths tracked by git. Untracked files, dotfiles, and paths outside the repository root return 404 even if they exist on disk. Ignore rules (`.gitignore`) are not a safeguard for files that are already tracked; only tracked, non-dotfile paths are served.

The tool runs on Linux, macOS, and Windows with Node.js 20 or newer.
Tested status: Locally checked; live matrix pending.

## Prerequisites

- Node.js 20 or newer (`node` on PATH).
- Git (`git` on PATH).
- A git working tree containing files to preview.

## Guided setup

The configurator inspects the working tree and generates `config.local.json`. It starts no server and refuses to replace an existing output file.

POSIX (Linux / macOS):

```bash
TOOL_DIR="$HOME/tools-and-scripts/development/git-preview-server"
node "$TOOL_DIR/configure.mjs" --root "$HOME/projects/documentation" --port 8123
```

Windows PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/development/git-preview-server'
node (Join-Path $ToolDir 'configure.mjs') --root (Join-Path $HOME 'projects/documentation') --port 8123
```

## Manual setup

Copy `config.example.json` to `config.local.json` and adjust values:

POSIX (Linux / macOS):

```bash
TOOL_DIR="$HOME/tools-and-scripts/development/git-preview-server"
CONFIG_PATH="$TOOL_DIR/config.local.json"
cp "$TOOL_DIR/config.example.json" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

Windows PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/development/git-preview-server'
$ConfigPath = Join-Path $ToolDir 'config.local.json'
Copy-Item (Join-Path $ToolDir 'config.example.json') $ConfigPath
notepad $ConfigPath
```

Replace `CUSTOMIZE:` values. Set `root` to an absolute path of a git working tree top level, or leave it empty to serve the git top level of whatever directory `serve.mjs` starts in.

## Inputs

Configuration is loaded only when `--config PATH` is explicitly supplied. Settings resolve in order: command-line options, then an explicit configuration file (`--config PATH`), then documented defaults.

| Setting | Flag | Default | Purpose |
|---|---|---|---|
| `root` | `--root PATH` | Git top level of current working directory | Absolute path to the top level of the git working tree |
| `host` | `--host ADDRESS` | `127.0.0.1` | IP address literal to listen on (`127.0.0.1`, `::1`) |
| `port` | `--port NUMBER` | `8123` | TCP port to listen on; use `0` to allocate any free port |
| N/A | `--allow-non-loopback` | disabled | CLI-only opt-in flag required when `--host` is not loopback |
| N/A | `--dry-run` | disabled | CLI-only flag to resolve settings and query git without listening |

`--allow-non-loopback` is accepted only on the command line, never in configuration files. If placed in a configuration file passed to `--config`, the server rejects the configuration.

## Permissions

Runs entirely as an ordinary unprivileged user. No elevation is required. The account needs read access to the git repository files and the `.git` directory.

## Dry run

Use `--dry-run` to verify working tree detection, check configuration, and see the number of visible files without binding a TCP port:

POSIX:

```bash
TOOL_DIR="$HOME/tools-and-scripts/development/git-preview-server"
CONFIG_PATH="$TOOL_DIR/config.local.json"
node "$TOOL_DIR/serve.mjs" --config "$CONFIG_PATH" --dry-run
```

Windows PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/development/git-preview-server'
$ConfigPath = Join-Path $ToolDir 'config.local.json'
node (Join-Path $ToolDir 'serve.mjs') --config $ConfigPath --dry-run
```

The dry run prints the resolved repository root, listening URL, and count of servable tracked files.

## Changes made

The server makes no changes to files on disk or git repository state. All HTTP endpoints are read-only.

`configure.mjs` creates only the requested local configuration file (`config.local.json`).

## Safeguard reasoning

- Loopback-only default: binding to `127.0.0.1` prevents remote devices on the local network or internet from accessing previewed files.
- Non-loopback opt-in flag: binding to an external or wildcard address (`0.0.0.0`) requires `--allow-non-loopback` on the command line. This flag cannot be stored in a config file, preventing accidental long-term exposure.
- Host header check: the `Host` header must be an IP literal or `localhost`. External domain names are rejected with HTTP 403 to prevent DNS rebinding attacks.
- Tracked-files publication boundary: `git ls-files -z` runs on every request. Untracked files, dotfiles (including `.git/` and configuration dotfiles), and paths outside the repository root return 404 even if present on disk. Ignore rules (`.gitignore`) are not a safeguard for files that are already tracked or force-added to git; only tracked, non-dotfile paths are served.
- Symlink verification: symbolic links are followed only when the target is itself a tracked, non-dotfile path inside the working tree root.
- Traversal defense: path segments containing `..` or leading dots return 404. Resolved paths are checked against the root directory before opening.
- Fail-closed git query: if `git ls-files` encounters an error, the server returns HTTP 500 without opening any file.
- Cache suppression: all responses include `cache-control: no-store` and `x-content-type-options: nosniff` so reloads reflect disk changes immediately.
- Method restriction: only `GET` and `HEAD` requests are accepted. Other methods return HTTP 405.

## Rollback

No rollback is required because the server and dry-run mode are read-only.

To stop the server:
- Press `Ctrl+C` in the running terminal.
- Send `SIGINT` or `SIGTERM` to the process.

To clean up local configuration, delete `config.local.json`. The tracked `config.example.json` remains untouched.

## Troubleshooting

- `host ... is not a loopback address`: pass `--allow-non-loopback` on the command line if external access is intended.
- `... is not the top level of its git working tree`: specify the repository root where `.git` is located, not a subdirectory.
- `... is not inside a git working tree`: verify the specified directory is initialized with git (`git rev-parse --show-toplevel`).
- `port ... is already in use`: specify another port with `--port NUMBER` or use `--port 0` for an ephemeral port.
- `404 not found` for an existing file: confirm the file is tracked with `git ls-files <path>`. Untracked files and dotfiles are never served. Note that ignore rules do not prevent already-tracked files from being served.
- `unable to read tracked files`: verify `git` is on PATH and accessible from the server's working directory.

## Exit behavior

- `0`: `--help` displayed, `--dry-run` passed, or server stopped cleanly via `SIGINT` or `SIGTERM`.
- `1`: invalid command-line option, invalid JSON configuration, missing Git or Node.js runtime, directory is not a git working tree root, listen address or port failure, non-loopback address without `--allow-non-loopback`, or runtime server error.
