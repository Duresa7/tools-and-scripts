# Building an E2B template

A template is the machine image sandboxes start from: an operating system, tools and files, and optionally a process already running. A sandbox restores the template's snapshot rather than booting it. Build one when the same slow setup would otherwise repeat in every sandbox, for example a repository's toolchain and dependencies.

## Before building

- **Ask the user first.** Each template takes several GB of disk on the E2B host, and a self-hosted host does not reclaim it on its own.
- **Name it for its job**, lowercase with letters, digits, `-` and `_`. Building under a name that already exists replaces that template for every client of the deployment, so reuse a name only when the user asked for that template to be rebuilt.
- **Size it**: `--cpu-count` (default 2) and `--memory-mb` (an even number, default 1024) become the size of every sandbox made from it. Use the smallest size that runs the job.

## From a Dockerfile (CLI)

From the folder that holds the Dockerfile and the files it copies:

```bash
e2b template create <name> --dockerfile Dockerfile --cpu-count 2 --memory-mb 2048 --min-free-disk-mb 4096 < /dev/null
```

`--min-free-disk-mb` is the free space left on the sandbox's disk after the build; installs inside a sandbox need it. `--cmd '<command>'` starts a process in the snapshot (a server, a database) and `--ready-cmd '<command>'` must exit 0 before the snapshot is taken. A repository's own Dockerfile is often a good start; E2B builds the Linux userland from it and supplies the kernel.

Done when the command exits 0 and prints the `Sandbox.create('<name>')` example. Then prove it: create a sandbox from it, run the tool the template exists for, and kill the sandbox.

## With the SDK

The Python SDK builds from code, with the same options. It suits templates assembled step by step or from E2B's published template definitions:

```python
from e2b import Template
tpl = (Template()
       .from_python_image("3.12")
       .apt_install(["build-essential", "git"])
       .run_cmd("pip install --no-cache-dir uv", user="root"))
Template.build(tpl, alias="<name>", cpu_count=2, memory_mb=2048, min_free_disk_mb=4096,
               on_build_logs=lambda e: print(str(e), flush=True))
```

`from_image`, `from_dockerfile`, `copy`, `set_envs`, `set_start_cmd` and `git_clone` are the other common steps.

## When a build fails

| Message | Cause and next step |
|---|---|
| A step fails with `No space left on device` | The build's working space ran out. The space each step gets is a deployment limit, separate from `--min-free-disk-mb`; report the step to the user. |
| `cannot allocate memory` while starting or resuming a step | The host's sandbox memory pool cannot fit the build VM. Rebuild with a smaller `--memory-mb`. |
| The upload of copied files fails, naming `http://127.0.0.1:5008/...` | A self-hosted deployment hands out a local upload address. The user opens the SSH tunnel described in [SETUP.md](../SETUP.md#template-builds-that-copy-files), then the build is retried. |
| `500`, or `An internal error occurred` | Report the template name and build ID to the user; the server's logs hold the cause. |

## Removing a template

`e2b template delete <name> --yes < /dev/null`, only for a template the user agrees is no longer needed: every sandbox made from it later fails to create.
