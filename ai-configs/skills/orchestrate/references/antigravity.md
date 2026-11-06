# Antigravity workers

Engine: Antigravity CLI, `agy -p`, running Google Gemini models. Labels use the roster `name`, `gemini` by default: `gemini-builder`, `gemini-researcher`, `gemini-reviewer`, `gemini-judge`.

Gemini is cheap parallel capacity for independent, well-specified tasks, and a second opinion from a different model family for research and review.

## Command

Bash (Linux, macOS, and Git Bash on Windows, which is the shell Claude Code uses there):

```bash
cd <absolute working directory> && agy -p "<prompt>" \
  --model <model from roster> \
  --add-dir <absolute working directory> \
  --dangerously-skip-permissions \
  --mode <accept-edits|plan> \
  --print-timeout 8m \
  --output-format text > <run dir>/<task id>.log 2>&1
```

PowerShell (Windows harnesses that do not go through Bash):

```powershell
Set-Location "<absolute working directory>"
agy -p "<prompt>" `
  --model <model from roster> `
  --add-dir "<absolute working directory>" `
  --dangerously-skip-permissions `
  --mode <accept-edits|plan> `
  --print-timeout 8m `
  --output-format text *> "<run dir>\<task id>.log"
```

- `--dangerously-skip-permissions` and `--mode accept-edits` are both required for a builder: without them a headless run soft-denies tool actions and can finish having done nothing. Use `--mode plan` for `reviewer` and `judge` so the run cannot edit.
- `--print-timeout` must be set; the CLI default is 5 minutes and a builder task sized to 8 minutes will be cut off.
- The CLI uses the signed-in Antigravity account. Never add a second Google account to spread quota, and never reuse OAuth tokens from another tool.

## Model

Effort is baked into the model name, so do not pass `--effort`.

| Model | Use for |
|---|---|
| `gemini-3.8-flash-high` | Default. Independent builder pieces, breadth research, implementation-level review |
| `gemini-3.1-pro-high` | Step up only when a flash attempt failed on a task that needs more reasoning, or the user asked for it |

## Parallel runs

Up to the roster `parallel` count at once; three concurrent runs sharing one config directory has been verified to complete cleanly. Label them `gemini-builder-1`, `gemini-builder-2`, and so on. They share one account and one quota, so parallelism buys wall-clock speed, not capacity. If more Gemini tasks are queued than the limit, run the first batch and wait before starting the next.

## Auth

`agy models` lists models only when signed in. An auth failure stops the run; report it.
