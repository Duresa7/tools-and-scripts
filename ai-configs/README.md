# AI-Configs

My personal skills, plugins, and guidance files for AI coding agents (Claude Code and Codex). The folders use the same layout that each tool uses, so you can copy or link a folder into a project or into your home directory.

## Layout

```
.claude/skills/   Claude Code skills. Each skill is a folder with a SKILL.md file.
.agents/skills/   Codex (OpenAI) skills. Same format: one folder per skill, with a SKILL.md file.
plugins/          Plugins. Each plugin is a folder with a .claude-plugin/plugin.json file.
guidance/         Notes that an agent can read, for example how to select a model and divide work.
```

## Skills

| Skill | What it does |
|---|---|
| [`1password-cli`](.claude/skills/1password-cli/SKILL.md) | Uses the 1Password CLI (`op`) and keeps secret values out of the agent output. |

## Install a skill

Link the skill folder into your user skills folder. Then the skill is available in all projects.

Run these commands from the `ai-configs` folder of this repository.

```bash
# Claude Code
ln -s "$PWD/.claude/skills/1password-cli" ~/.claude/skills/1password-cli

# Codex
ln -s "$PWD/.claude/skills/1password-cli" ~/.agents/skills/1password-cli
```

On Windows, use PowerShell with Developer Mode on, or use an elevated shell:

```powershell
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\1password-cli" -Target "$PWD\.claude\skills\1password-cli"
```

## License

MIT. See [LICENSE](LICENSE).
