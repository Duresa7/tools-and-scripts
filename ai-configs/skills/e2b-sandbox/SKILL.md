---
name: e2b-sandbox
description: Run code in a throwaway E2B sandbox (a Firecracker microVM) instead of on this machine. Use for untrusted or unfamiliar code (an unknown repo's install or test scripts, unvetted packages, generated scripts), for work that should stay off this machine and its network, for building an E2B template, and whenever the user asks for a sandbox or E2B.
---

# E2B sandbox

A sandbox is a **throwaway** Linux microVM: its own disk, processes and network, created in about a second and gone when killed. Code that runs inside it never touches this machine, its files or the local network. Treat every sandbox as throwaway: create one per task, bring back what matters, kill it.

## Steps

1. **Check the client.** Run `e2b sandbox list`. A table header or `No sandboxes found` means the CLI and its variables are ready. If `e2b` is missing, or the call fails on authentication or an address, stop and work through [SETUP.md](SETUP.md) with the user, who supplies the API key.
2. **Pick a template.** `e2b template list` shows what this deployment offers, with each template's vCPUs and memory. Choose the smallest one that already has the tools the job needs; a template with the toolchain preinstalled beats installing it in every sandbox.
3. **Create the sandbox**, detached, and keep its ID:
   ```bash
   ID=$(e2b sandbox create <template> --detach --timeout 1800)
   ```
   `--detach` makes the call print only the ID; without it the CLI opens an interactive terminal. `--timeout` is in seconds (default 300); size it to the task. The sandbox is killed when it runs out.
4. **Put the files in.** One file: `cat app.py | e2b sandbox exec "$ID" 'cat > /home/user/app.py'`. A folder: `tar czf - -C <parent> <dir> | e2b sandbox exec "$ID" 'tar xzf - -C /home/user'`. A public repository: `e2b sandbox exec "$ID" 'git clone --depth 1 <url> /home/user/repo' < /dev/null`. Pass in only what the job needs; credentials and private keys stay on this machine.
5. **Run the work** with `e2b sandbox exec "$ID" '<command>' < /dev/null`. Options such as `--cwd <dir>` and `-b` go before the ID (`e2b sandbox exec --cwd /home/user/repo "$ID" '<command>'`); everything after the ID becomes part of the remote command. Every `exec` without piped input gets `< /dev/null`: when the shell's stdin is an open pipe, as in most agent harnesses, `exec` prints its output and then waits for input that never arrives. Output streams back and the remote exit code becomes the CLI's exit code, so judge each command by `$?`. Commands run as the template's default user (`user` in E2B's own templates), with password-less `sudo`.
6. **Bring the results back** through stdout: `e2b sandbox exec "$ID" 'cat /home/user/out.txt' < /dev/null > out.txt`, `tar czf - <dir>` for several files, `base64` for one binary file.
7. **Kill it**: `e2b sandbox kill "$ID"`. Done when `e2b sandbox list` no longer shows the ID. Every sandbox you created gets killed, including after a failure.

## Reference

**Network.** A sandbox reaches the internet (package registries, GitHub, container registries). E2B denies it every private range (10/8, 172.16/12, 192.168/16) and link-local, so work that needs a service on the local network belongs outside the sandbox.

**Disk.** Keep large downloads and builds under `/home/user`: `/tmp` is usually a small in-memory filesystem. `df -h /` inside shows the space a template leaves free.

**Packages.** `sudo apt-get install -y <package>` adds system packages. Ubuntu-based templates refuse system-wide `pip` installs, so Python packages go in a virtual environment: `python3 -m venv /home/user/.venv && /home/user/.venv/bin/pip install <package>`, or `uv venv` then `uv pip install <package>` where `uv` is present.

**Limits.** A create that answers `400: Timeout cannot be greater than N hours` has hit the deployment's lifetime cap; lower `--timeout`. `503: not enough capacity` or `Failed to place sandbox` means the host is full or busy: retry after a few seconds, after killing sandboxes you no longer need.

**Long jobs.** `e2b sandbox exec -b "$ID" '<command> > /home/user/job.log 2>&1' < /dev/null` starts work in the background and returns at once; check on it with a later `exec` that reads the log. For work that spans sessions, create with `--lifecycle.ontimeout pause --lifecycle.autoresume`: the sandbox pauses at its timeout with memory and files intact, and the next command against its ID resumes it. A paused sandbox still holds disk on the host, so kill it once the work is finished.

**A port inside.** When `E2B_SANDBOX_URL` is set (self-hosted E2B), reach a server on port 8080 with `curl -H "E2b-Sandbox-Id: $ID" -H "E2b-Sandbox-Port: 8080" "$E2B_SANDBOX_URL/"`. Otherwise its address is `https://8080-$ID.<domain>`, where the domain is `$E2B_DOMAIN`, or `e2b.app` on E2B Cloud.

## More

- [references/sdk.md](references/sdk.md): the Python SDK, for egress allow lists, notebook-style analysis with charts (Code Interpreter), and driving or showing a desktop (Desktop).
- [references/templates.md](references/templates.md): building a template from a Dockerfile or with the SDK.
- [references/windows.md](references/windows.md): the steps above in PowerShell, for any session whose shell is PowerShell.
