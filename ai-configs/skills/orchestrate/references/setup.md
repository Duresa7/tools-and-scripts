# Writing config.local.toml

Read this when `config.local.toml` is missing or names a CLI that is not installed. The goal is a roster that matches the machine, written once, then left alone.

## Steps

1. Copy `config.example.toml` to `config.local.toml` in this skill's folder. The file is gitignored by the repository's `config.local.toml` rule, so nothing here is committed.
2. For each CLI, confirm it is installed and signed in. Remove the block for any engine the machine does not have.

   | Engine | Installed? | Signed in? |
   |---|---|---|
   | codex | `codex --version` | `codex login status` (with `CODEX_HOME` set for each login) |
   | claude | `claude --version` | `claude auth status` (with `CLAUDE_CONFIG_DIR` set for each login) |
   | antigravity | `agy --version` | `agy models` lists models only when signed in |

3. For each extra login of the same engine, add another `[[account]]` block with its own `home` (Codex) or `config_dir` (Claude). A second login is a separate directory the CLI was signed into, for example `CODEX_HOME=~/.codex_second codex login`. Never copy tokens between directories; sign in once per directory.
4. Set `models` per login to what that login can actually use. If one Claude login has access to a model the others lack, list it only there and add `roles = ["cleaner", "reviewer", "judge"]` so the orchestrator spends it on judgment work rather than bulk building.
5. Set `parallel` to a number you have seen complete cleanly. Three concurrent runs per Claude or Antigravity login has been verified on the maintainer's machine; start at 1 for a Codex login.
6. Leave `scratch_dir` empty unless the system temp directory is unsuitable (small, cleaned aggressively, or on a slow network share).

## Paths

Write paths as the shell will read them. `~` expands in Bash, including Git Bash on Windows. In PowerShell use `$HOME\.codex` style or an absolute path. On Windows the default directories are `%USERPROFILE%\.codex` and `%USERPROFILE%\.claude`.

## Checking the roster

Before the first real run, dispatch one trivial task per account (write a file containing a fixed string, then confirm it exists). A roster entry that fails this check is removed or fixed before any real work is sent to it.
