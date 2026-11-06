# Future tool template

Use this checklist when adding a tool. A reader should be able to download one folder, understand its risks, create local configuration, and run its tests without copying code from another category.

## Folder layout

```text
category/tool-name/
├── README.md
├── tool entry point
├── configure entry point
├── config.example.<native-format>
└── tests/
```

Keep shared code inside the tool folder. If two tools need the same code, either accept the duplication or create a separately documented package with its own version and tests.

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

State the supported platform, minimum runtime, required privilege, files or services changed, preview path, rollback boundary, and tested status. Don't claim operating-system compatibility until the result is recorded in `COMPATIBILITY.md`.

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

Keep tool tests under `tool-name/tests/`. Cover config precedence, configurator output, non-overwrite behavior, credential exclusion, CLI help, failure cleanup, dry-run gates, and the state-changing boundary. Add repository-level tests only for rules shared by every tool.

Run `python check.py` from the repository root before a checkpoint commit. A missing checker is a failed local verification, not a skipped test.
