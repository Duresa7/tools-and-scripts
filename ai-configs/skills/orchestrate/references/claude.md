# Claude workers

Engine: Claude Code CLI, `claude -p`. Labels: `claude-builder`, `claude-researcher`, `claude-reviewer`, `claude-judge`, `claude-cleaner`.

Claude is the engine for judgment and taste: careful review, writing, subtle refactors, and the final quality pass. It is also the only engine with a read-only permission mode, which is what makes `reviewer` and `judge` runs trustworthy.

## Command

Bash (Linux, macOS, and Git Bash on Windows, which is the shell Claude Code uses there):

```bash
cd <absolute working directory> && CLAUDE_CONFIG_DIR=<config_dir from roster> \
  claude -p "<prompt>" \
  --model <model> --effort <level> \
  --permission-mode <mode> \
  --allowedTools <allowed_tools from roster> \
  --output-format text < /dev/null > <run dir>/<task id>.log 2>&1
```

PowerShell (Windows harnesses that do not go through Bash):

```powershell
Set-Location "<absolute working directory>"
$env:CLAUDE_CONFIG_DIR = "<config_dir from roster>"
$null | claude -p "<prompt>" `
  --model <model> --effort <level> `
  --permission-mode <mode> `
  --allowedTools <allowed_tools from roster> `
  --output-format text *> "<run dir>\<task id>.log"
```

- Omit `CLAUDE_CONFIG_DIR` entirely when the roster's `config_dir` is empty; the CLI then uses its default, which is normally the account the orchestrator itself is on.
- Stdin must be closed (`< /dev/null`, or `$null |` in PowerShell). Without it the CLI waits several seconds for input on every run.
- `--allowedTools` pre-approves tools so the headless run does not stall on a permission prompt. Pass the roster's `allowed_tools` list plus anything the task needs, for example `"Bash(npm test:*)"`. Omit the flag when the list is empty.
- Permission mode by role:

  | Role | `--permission-mode` |
  |---|---|
  | `builder`, `cleaner` | `acceptEdits`; `bypassPermissions` when the task needs broad shell access |
  | `researcher` | `acceptEdits` when it writes a note, else `plan` |
  | `reviewer`, `judge` | `plan`: read-only, verified to read files, report findings, and change nothing. A reviewer that can edit will quietly fix what it finds instead of reporting it. |

## Model and effort

Two independent choices. Effort scales within a model; raise it before switching model. Every level is allowed; the orchestrator picks per task.

| Model | Use for |
|---|---|
| `claude-sonnet-5` | Default for almost everything: mechanical work, spec'd features, tests, docs, data pulls, single-module refactors |
| `claude-opus-5-5` | Judgment-heavy work: reviews where taste matters, debugging with unclear cause, cross-module design, ambiguous requirements |
| `claude-fable-5-1` | The most capable and most expensive tier. Only on a roster login that lists it. Best spent on one `cleaner` pass over a finished change set, or a `reviewer`/`judge` run whose verdict decides the whole run. Not a bulk builder. |

| Effort | Use for |
|---|---|
| `low` | Mechanical work with a complete spec |
| `medium` | Clear spec, low ambiguity; the Sonnet floor for anything that ships |
| `high` | Multi-step work, some ambiguity, needs to check its own output |
| `xhigh` | Hard problems where a wrong approach is expensive to unwind |
| `max` | Opus or Fable, when correctness matters more than cost or a `xhigh` attempt failed |

Fable runs long on hard tasks; give it the whole spec up front and expect a single run to take longer than a Sonnet run of the same task. Prompts for Fable work best when they state the goal and the constraints and leave the method to it, rather than prescribing steps.

## Parallel runs

A login may run up to its roster `parallel` count at once. Label parallel runs of the same worker `claude-builder-1`, `claude-builder-2`, and so on in the ledger. Runs on the same login share its quota, so use parallelism for wall-clock speed on independent tasks, not for capacity. Workers on the orchestrator's own login also compete with the orchestrator for rate limits; put bulk work on another login when the roster has one.

## Auth

`CLAUDE_CONFIG_DIR=<config_dir> claude auth status`. An auth failure on any login stops the run; report it rather than shifting that task to another login.
