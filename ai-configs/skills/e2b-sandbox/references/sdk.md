# E2B Python SDK

The CLI covers create, run, files through stdin and stdout, and kill. Reach for the SDK when a task needs one of the features below. Install it in a virtual environment, not the system Python:

```bash
python3 -m venv .e2b-venv && .e2b-venv/bin/pip install e2b          # add e2b-code-interpreter or e2b-desktop as needed
```

The SDK reads the same `E2B_API_KEY`, `E2B_DOMAIN` and `E2B_SANDBOX_URL` variables as the CLI. Every example ends with `kill()`; keep it in a `finally` block.

## Egress allow list

For the least-trusted code, such as an unknown package's install step, lock the sandbox to the hosts the task needs. Everything else fails to connect.

```python
from e2b import Sandbox
sbx = Sandbox.create("<template>", timeout=1800,
                     network={"deny_out": ["0.0.0.0/0"], "allow_out": ["pypi.org", "files.pythonhosted.org"]})
try:
    print(sbx.commands.run("pip install <package>").stdout)
finally:
    sbx.kill()
```

`allow_out` takes host names and CIDRs. The CLI has no flag for this.

## Analysis with state between calls (Code Interpreter)

For data work: variables survive from one `run_code` to the next, and a plotted chart comes back as PNG data. Needs a `code-interpreter-v1` template (check `e2b template list`) and `pip install e2b-code-interpreter`.

```python
from e2b_code_interpreter import Sandbox
sbx = Sandbox.create(timeout=900)
try:
    sbx.run_code("import pandas as pd\ndf = pd.DataFrame({'x': [1, 2, 3]})")
    r = sbx.run_code("df['x'].sum()")
    print(r.text, r.error)                    # text holds the last expression's value
    r = sbx.run_code("df.plot()")
    pngs = [res.png for res in r.results if res.png]   # base64 PNG strings
finally:
    sbx.kill()
```

`language="javascript"` (or `r`, `java`, `bash`) on `run_code` picks another kernel. A file the code needs goes in with `sbx.files.write(path, data)`.

## Driving a desktop (Desktop)

For GUI work: an Xfce desktop with Firefox. Needs a `desktop` template and `pip install e2b-desktop`.

```python
from e2b_desktop import Sandbox
d = Sandbox.create(timeout=1800)
try:
    d.launch("firefox")
    d.wait(3000)
    png = d.screenshot()                      # bytes; save it and look at it to see the screen
    d.left_click(400, 300); d.write("hello"); d.press("enter")
finally:
    d.kill()
```

Read the screen with `screenshot()` after each action rather than assuming where things are.

**Letting the user watch or take over.** Call `d.stream.start()` and give the user a link. When `E2B_DESKTOP_VIEWER_URL` is set, the link is

```
$E2B_DESKTOP_VIEWER_URL/<sandbox id>/vnc.html?autoconnect=1&resize=scale&path=<sandbox id>/websockify
```

Otherwise it is `d.stream.get_url()`. Keep the sandbox alive while the user has the link open.
