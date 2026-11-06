# Codex workers

Engine: OpenAI Codex CLI, `codex exec`. Labels: `codex-builder`, `codex-researcher`, `codex-reviewer`, `codex-judge`, `codex-cleaner`.

Codex is the default builder: fast, cheap, and reliable on mechanical and clear-spec work. Round-robin builder tasks across the Codex accounts in the roster starting from the first, since there is no memory of earlier sessions to balance against.

## Command

Bash (Linux, macOS, and Git Bash on Windows, which is the shell Claude Code uses there):

```bash
CODEX_HOME=<home from roster> codex exec \
  --cd <absolute working directory> \
  --skip-git-repo-check \
  -c model=<model from roster> \
  -c model_reasoning_effort=<level> \
  -c service_tier=default \
  --output-last-message <run dir>/<task id>.md \
  "<prompt>" > <run dir>/<task id>.log 2>&1
```

PowerShell (Windows harnesses that do not go through Bash):

```powershell
$env:CODEX_HOME = "<home from roster>"
codex exec `
  --cd "<absolute working directory>" `
  --skip-git-repo-check `
  -c model=<model from roster> `
  -c model_reasoning_effort=<level> `
  -c service_tier=default `
  --output-last-message "<run dir>\<task id>.md" `
  "<prompt>" *> "<run dir>\<task id>.log"
```

- `--cd` is the directory the worker operates in: the repo root when there is one, otherwise the directory holding the relevant files, or a fresh directory under the run's scratch folder for throwaway work.
- `service_tier=default` keeps the CLI on the standard tier. Leave it alone.
- Approvals and sandboxing come from that login's `config.toml`. A roster login used for building needs approvals off and a writable sandbox, so only point it at directories you are willing to have edited. For `reviewer` and `judge` roles add `-s read-only` so the run cannot edit anything.
- The last message lands in the `.md` file; the full transcript in the `.log`. Read the `.md` first.

## Reasoning effort

Pick per task. Do not default to high; step up on retry.

| Level | Use for |
|---|---|
| `low` | Mechanical work: rename, move, fix a typo, update a config value, write a file from a complete spec, run a command and report output |
| `medium` | Ordinary work with a clear spec: add an endpoint, write tests for existing code, refactor within one module, draft a document from an outline |
| `high` | Cross-module changes, debugging with an unclear cause, research where sources must be weighed, anything where the approach itself is uncertain |
| `xhigh` | A `high` attempt failed and the task is genuinely hard, or the user asked for it |

## Auth

`CODEX_HOME=<home> codex login status`. An auth failure on any login stops the run; report it rather than shifting that task to another login.
