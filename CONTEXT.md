# Context

Terms this repository uses for its own structure.

**Tool**: one self-contained folder at `<category>/<tool-name>/` with a README, a tool manifest, an entry point, tests, and usually a configurator. A reader can download the folder alone and run it. _Avoid_: script (a tool may hold several), project (except for the Ansible tools in `fleet/`).

**Category**: the top-level folder a tool lives in, chosen by the job the tool does: `monitoring`, `fleet`, `security`, `maintenance`, or `utilities`. Platform is not a category; the README's status table records it.

**Tool manifest**: the `tool.toml` file in each tool folder. It lists the help commands `check.py` runs and the local files git must ignore. `tool_catalog.py` reads every manifest, so shared checks never keep their own list of tool paths.

**Entry point**: the file a user runs. It is named after the tool folder (`prometheus_target_check.py`, `compose-service-update.sh`), except PowerShell, which uses Verb-Noun names (`Test-InstallerSignature.ps1`).

**Configurator**: the `configure.*` script that writes a tool's local configuration from its annotated example.

**Local file**: a file the user creates from an example, such as `config.local.toml` or `inventory/hosts.yml`. Git must ignore it.
