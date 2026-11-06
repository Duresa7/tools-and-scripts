# Adding a tool

Use this checklist when adding a tool. A reader should be able to download one folder, understand its risks, create local configuration, and run its tests without copying code from another category.

## Folder layout

```text
category/tool-name/
├── README.md
├── tool.toml
├── <entry point named after the folder>
├── configure.<ext>
├── config.example.<native-format>
└── tests/
```

Pick the category by the job the tool does, not the platform it runs on:

| Category | Put a tool here when it |
|---|---|
| `monitoring/` | checks health, probes a service, collects metrics or logs, or routes alerts |
| `fleet/` | is an Ansible project that changes many hosts from one controller |
| `security/` | enforces access policy, locks a system down, or keeps an allowlist or blocklist current |
| `maintenance/` | makes a one-off change, migration, backup, or upkeep task on one system |
| `utilities/` | fits none of the above |

The README's status table records the platform, so don't split a category by operating system.

Name the entry point after the folder: `tool_name.py` (snake case) for Python, `tool-name.sh`, `tool-name.mjs`, or `tool-name.bat` for Bash, Node, and batch. PowerShell entry points use an approved Verb-Noun name instead, such as `Test-InstallerSignature.ps1`; a tool with several scripts gives each its own verb. Helper files keep role names: `configure.*`, `lib/`, `systemd/`, `playbooks/`.

Keep shared code inside the tool folder. If two tools need the same code, either accept the duplication or create a separately documented package with its own version and tests.

## Tool manifest

`tool.toml` tells the repository checks what to run. `check.py` and the repository tests find every tool through it, so you never add a new tool's path to a shared list.

```toml
# Commands check.py runs to confirm the scripts start. Python and Node run
# directly; Bash runs inside the Linux checks. PowerShell is parsed, not run.
help = [
  ["tool_name.py", "--help"],
  ["configure.py", "--help"],
]
# Files the user creates locally. The tests confirm git ignores each one.
local_files = ["config.local.toml"]
# Set to false only when the tool has no inputs to configure.
configurator = true
```

## README sections

Use this linked order:

1. Use case
2. Prerequisites
3. Guided setup
4. Manual setup
5. Inputs
6. Permissions
7. Dry run
8. Changes made
9. Safeguard reasoning
10. Rollback
11. Troubleshooting
12. Exit behavior

State the supported platform, minimum runtime, required privilege, files or services changed, preview path, rollback boundary, and tested status. Don't claim operating-system compatibility until the result is recorded in [compatibility.md](compatibility.md).

## Configuration

- Use the runtime's standard format: strict shell `KEY=value`, JSON, TOML, or YAML.
- Track `config.example.*` and ignore `config.local.*`.
- Put `CUSTOMIZE:` beside every address, path, account, identifier, policy, timeout, and environment-variable name owned by the user.
- Resolve values in this order: command line, explicit local config, documented default.
- Keep passwords, tokens, private keys, and recovery codes out of config and command arguments. Read them from a named environment variable or hidden prompt.
- Refuse to replace an existing local config unless the user gives a separate, documented overwrite option.

## Safety metadata

Document these facts near the first runnable command:

| Field | Required answer |
|---|---|
| Platform | Exact operating systems or runtime family |
| Privilege | Ordinary user, one elevated operation, or service account |
| State change | Exact files, records, services, or remote objects changed |
| Preview | Dry run, check mode, saved-input mode, or not available |
| Rollback | Automatic boundary and manual recovery action |
| Exit codes | Meaning of every nonzero status |

An operation that can remove access, interrupt networking, replace data, or create remote objects needs an explicit gate. Use an exact phrase when one stale flag could turn a preview command into a destructive run.

## Comments

Comment user inputs and non-obvious safety decisions. Don't narrate assignments, loops, or library calls that are already clear from the code. A useful comment explains why a redirect is rejected, why a temporary file is installed atomically, or why a target list must be complete.

## Tests

Keep tool tests under `tool-name/tests/`. Cover config precedence, configurator output, non-overwrite behavior, credential exclusion, CLI help, failure cleanup, dry-run gates, and the state-changing boundary. Add repository-level tests only for rules shared by every tool, and reach a tool through `tool_catalog.tool("tool-name")` rather than a fixed path.

Run `python check.py` from the repository root before a checkpoint commit. A missing checker is a failed local verification, not a skipped test.
