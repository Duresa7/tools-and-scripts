# Set up the E2B sandbox skill on a machine

Do this once per machine. Afterwards Claude Code and Codex on that machine can create throwaway E2B sandboxes, run code in them, and kill them, with the [`e2b-sandbox`](SKILL.md) skill telling them how.

A machine needs three things: the `e2b` CLI, three environment variables that point it at your E2B deployment, and the skill linked into each agent's skills folder.

## What you need

- **An E2B deployment.** Either [E2B Cloud](https://e2b.dev) with an API key from its dashboard, or a self-hosted one such as [E2B Embed](https://github.com/e2b-dev/runtime/tree/main/embed), with its team API key.
- **Node.js 22.19 or newer** for the CLI. Check with `node --version`.
- **For a self-hosted deployment on a private network:** the machine is on that network or its VPN, and uses the DNS server that resolves the deployment's names.

Anyone holding the API key can create and delete every sandbox and template in the deployment. Keep it in your password manager and in the one place on each machine described below.

## 1. Install the CLI

The same command on macOS, Linux and Windows:

```bash
npm install -g @e2b/cli
e2b --version
```

## 2. Set the variables

| Variable | E2B Cloud | Self-hosted |
|---|---|---|
| `E2B_API_KEY` | your API key | the team API key |
| `E2B_DOMAIN` | leave unset | the deployment's domain |
| `E2B_SANDBOX_URL` | leave unset | `https://` plus the host that serves sandbox traffic |
| `E2B_DESKTOP_VIEWER_URL` | leave unset | optional, the Desktop viewer's address if you run one |

`E2B_SANDBOX_URL` matters for self-hosted setups. Without it, the CLI and SDK address every sandbox as `<port>-<sandbox id>.<domain>`, which needs wildcard DNS. With it, they send all sandbox traffic to one host and name the sandbox in request headers.

My homelab's values ([E2B on e2b-01](https://github.com/Duresa7/Homelab/tree/main/Platforms/E2B)):

```
E2B_DOMAIN=e2b.alphasecunited.com
E2B_SANDBOX_URL=https://sandbox.e2b.alphasecunited.com
E2B_DESKTOP_VIEWER_URL=https://desktop.e2b.alphasecunited.com
```

Agents run their commands in non-interactive shells, so the variables must be set where those shells read them.

### macOS (zsh)

Keep the values in their own file and load it from `~/.zshenv`, which every zsh reads (`~/.zshrc` is read by interactive shells only):

```bash
mkdir -p ~/.config/e2b && touch ~/.config/e2b/env && chmod 600 ~/.config/e2b/env
cat >> ~/.config/e2b/env <<'EOF'
export E2B_DOMAIN=e2b.alphasecunited.com
export E2B_SANDBOX_URL=https://sandbox.e2b.alphasecunited.com
export E2B_DESKTOP_VIEWER_URL=https://desktop.e2b.alphasecunited.com
EOF
printf 'export E2B_API_KEY=%s\n' "$(op read 'op://<vault>/<item>/credential')" >> ~/.config/e2b/env
echo '[ -r "$HOME/.config/e2b/env" ] && . "$HOME/.config/e2b/env"' >> ~/.zshenv
```

The `op read` line takes the key from the 1Password CLI so it never sits in your shell history; without `op`, open the file in an editor and add `export E2B_API_KEY=...` by hand.

### Linux (bash)

The same file, loaded near the top of `~/.bashrc`, above the line that returns early for non-interactive shells (`case $- in` or `[ -z "$PS1" ] && return`):

```bash
mkdir -p ~/.config/e2b && touch ~/.config/e2b/env && chmod 600 ~/.config/e2b/env
# add the same export lines as on macOS, then:
sed -i '1a [ -r "$HOME/.config/e2b/env" ] \&\& . "$HOME/.config/e2b/env"' ~/.bashrc
```

### Windows (PowerShell)

User environment variables, read by every new process:

```powershell
[Environment]::SetEnvironmentVariable('E2B_DOMAIN', 'e2b.alphasecunited.com', 'User')
[Environment]::SetEnvironmentVariable('E2B_SANDBOX_URL', 'https://sandbox.e2b.alphasecunited.com', 'User')
[Environment]::SetEnvironmentVariable('E2B_DESKTOP_VIEWER_URL', 'https://desktop.e2b.alphasecunited.com', 'User')
[Environment]::SetEnvironmentVariable('E2B_API_KEY', (op read 'op://<vault>/<item>/credential'), 'User')
```

Without `op`, set `E2B_API_KEY` in **System Properties > Environment Variables** instead of typing it into the console.

Then restart Claude Code, Codex and any terminal, so they start with the new variables.

## 3. Link the skill

Clone this repository once, then link the skill folder into each agent's skills folder, the same way as the other skills in [`ai-configs`](../../README.md). Run these from the `ai-configs` folder of the clone.

macOS and Linux:

```bash
mkdir -p ~/.claude/skills ~/.agents/skills
ln -s "$PWD/skills/e2b-sandbox" ~/.claude/skills/e2b-sandbox    # Claude Code
ln -s "$PWD/skills/e2b-sandbox" ~/.agents/skills/e2b-sandbox    # Codex
```

Windows, in PowerShell with Developer Mode on, or in an elevated shell:

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.claude\skills", "$HOME\.agents\skills" | Out-Null
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\e2b-sandbox" -Target "$PWD\skills\e2b-sandbox"
New-Item -ItemType SymbolicLink -Path "$HOME\.agents\skills\e2b-sandbox" -Target "$PWD\skills\e2b-sandbox"
```

A `git pull` in the clone updates the skill everywhere it is linked.

## 4. Check it

In a new terminal:

```bash
e2b template list
ID=$(e2b sandbox create base --detach)
e2b sandbox exec "$ID" 'echo hello from the sandbox' < /dev/null
e2b sandbox kill "$ID"
```

```powershell
e2b template list
$ID = (e2b sandbox create base --detach).Trim()
$null | e2b sandbox exec $ID 'echo hello from the sandbox'
e2b sandbox kill $ID
```

`template list` shows the deployment's templates, `exec` prints the greeting, and `kill` confirms. Then ask an agent something like "use a sandbox to run `python3 -c 'print(6*7)'`": it should create a sandbox, print 42, and kill the sandbox.

## Optional extras

**The Python SDK** is needed only for the features in [references/sdk.md](references/sdk.md) (egress allow lists, Code Interpreter, Desktop) and for SDK template builds. Agents install it into a virtual environment when they need it; `e2b-code-interpreter` and `e2b-desktop` are separate packages.

### Template builds that copy files

A self-hosted Embed deployment hands out `http://127.0.0.1:5008/...` as the upload address for files a template build copies in, so those builds run with an SSH tunnel open to the E2B host for their whole duration:

```bash
ssh -N -L 5008:127.0.0.1:5008 dkadi@192.168.80.30      # my homelab's e2b-01
```

Builds without copied files, and E2B Cloud, need no tunnel.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `e2b: command not found` | npm's global `bin` folder is not on `PATH`. `npm prefix -g` shows the folder; add its `bin` (Windows: the folder itself) to `PATH`. |
| An error naming `<port>-<sandbox id>.<domain>`, or `dns error` | `E2B_SANDBOX_URL` is not set in the shell the agent uses. Recheck step 2 in a new terminal with `echo $E2B_SANDBOX_URL` or `$env:E2B_SANDBOX_URL`. |
| `401` or an invalid key error | `E2B_API_KEY` is missing or wrong. |
| Calls time out | The machine is off the network or VPN, or uses a DNS server that does not know the deployment's names. |
| `exec` prints its output, then hangs | Its stdin is an open pipe. Add `< /dev/null` (bash, zsh) or start the line with `$null |` (PowerShell). The skill tells agents to. |
| `400: Timeout cannot be greater than N hours` | The deployment caps sandbox lifetime; use a smaller `--timeout`. |
| The dashboard's "Create a Sandbox" button opens a 404 | That button is a link to E2B's online docs, which a self-hosted dashboard does not serve. Create sandboxes with the CLI or SDK. |

## Update or remove

- Update the CLI with `npm install -g @e2b/cli@latest`, and the skill with `git pull` in the clone.
- To remove: delete the two links in `~/.claude/skills` and `~/.agents/skills`, the `~/.config/e2b/env` file and the line that loads it (or the four Windows user variables), then `npm uninstall -g @e2b/cli`.
