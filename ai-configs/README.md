# AI-Configs

My personal skills and guidance files for AI coding agents (Claude Code and Codex). Both agents read the same skill format, so each skill lives once under `skills/` and you link it into whichever agent's skills folder you use.

## Layout

```
skills/     Skills for Claude Code and Codex. Each skill is a folder with a SKILL.md file.
guidance/   Notes that an agent can read, for example how to select a model and divide work.
```

## Skills

| Skill | What it does |
|---|---|
| [`1password-cli`](skills/1password-cli/SKILL.md) | Uses the 1Password CLI (`op`) and keeps secret values out of the agent output. |
| [`orchestrate`](skills/orchestrate/SKILL.md) | Turns the agent into an orchestrator that plans, delegates to headless Codex, Claude Code, and Antigravity workers, verifies, retries, and reports once. Accounts and models come from a gitignored `config.local.toml`; see `config.example.toml`. |

## Install a skill

Link the skill folder into your user skills folder. Then the skill is available in all projects.

Run these commands from the `ai-configs` folder of this repository.

```bash
# Claude Code
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/1password-cli" ~/.claude/skills/1password-cli

# Codex
mkdir -p ~/.agents/skills
ln -s "$PWD/skills/1password-cli" ~/.agents/skills/1password-cli
```

On Windows, use PowerShell with Developer Mode on, or use an elevated shell:

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.claude\skills" | Out-Null
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\1password-cli" -Target "$PWD\skills\1password-cli"
```

Repeat with `orchestrate` in place of `1password-cli` for the orchestrate skill. That skill also needs a `config.local.toml` next to its `SKILL.md`: copy `config.example.toml`, list the CLI logins on your machine, and keep the file out of git (the repository already ignores it).

## License

MIT. See [LICENSE](../LICENSE).
