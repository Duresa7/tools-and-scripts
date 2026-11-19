# E2B sandbox steps in PowerShell

The same seven steps as SKILL.md, written for Windows PowerShell 5.1 and PowerShell 7. The remote command after `exec` is always Linux shell, because it runs inside the sandbox.

Four PowerShell behaviors shape these commands:

- A pipe into a program carries text, so raw bytes (a tar or zip stream) get corrupted. Files travel as base64 text, which survives.
- In 5.1, `>` writes UTF-16. Save output with `Set-Content -Encoding utf8` (text) or decode base64 to bytes.
- When the shell's stdin is an open pipe, as in most agent harnesses, `exec` waits for input after its command finishes. Every `exec` without piped input starts with `$null |`.
- Before 7.3, double quotes inside an argument to a program are dropped. Write the remote command in single quotes with no double quotes inside; for anything more involved, send a script file (step 4) and run it with `bash`.

## Steps

1. **Check the client.** `e2b sandbox list` prints a table header or `No sandboxes found`. The variables are in `$env:E2B_API_KEY`, `$env:E2B_DOMAIN` and `$env:E2B_SANDBOX_URL`; if they are empty, work through [SETUP.md](../SETUP.md) with the user.

2. **Pick a template** with `e2b template list`.

3. **Create the sandbox** and keep its ID:
   ```powershell
   $ID = (e2b sandbox create <template> --detach --timeout 1800).Trim()
   ```

4. **Put the files in**, as base64:
   ```powershell
   # one file (any type)
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\app.py")) | e2b sandbox exec $ID 'base64 -d > /home/user/app.py'

   # a folder: zip it, send it, unpack it
   Compress-Archive -Path .\myproj -DestinationPath "$env:TEMP\myproj.zip" -Force
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$env:TEMP\myproj.zip")) | e2b sandbox exec $ID 'base64 -d > /home/user/in.zip && python3 -m zipfile -e /home/user/in.zip /home/user && rm /home/user/in.zip'
   Remove-Item "$env:TEMP\myproj.zip"
   ```
   A public repository: `$null | e2b sandbox exec $ID 'git clone --depth 1 <url> /home/user/repo'`. A script for an involved command: write it locally with Linux line endings, send it like any file, then `$null | e2b sandbox exec $ID 'bash /home/user/run.sh'`.

5. **Run the work**: `$null | e2b sandbox exec $ID '<command>'`, then read `$LASTEXITCODE` for the remote exit code. Options go before the ID: `$null | e2b sandbox exec --cwd /home/user/myproj $ID '<command>'`.

6. **Bring the results back.**
   ```powershell
   # text
   $null | e2b sandbox exec $ID 'cat /home/user/out.txt' | Set-Content -Encoding utf8 .\out.txt

   # any file, including binary
   $b64 = ($null | e2b sandbox exec $ID 'base64 -w0 /home/user/report.png') -join ''
   [IO.File]::WriteAllBytes("$PWD\report.png", [Convert]::FromBase64String($b64))
   ```

7. **Kill it**: `e2b sandbox kill $ID`. Done when `e2b sandbox list` no longer shows the ID.

## A port inside

`curl` in Windows PowerShell 5.1 is `Invoke-WebRequest`. With `E2B_SANDBOX_URL` set:

```powershell
Invoke-WebRequest -UseBasicParsing "$env:E2B_SANDBOX_URL/" -Headers @{ 'E2b-Sandbox-Id' = $ID; 'E2b-Sandbox-Port' = '8080' }
```

`curl.exe` with `-H` works too, as on Linux.

## SDK on Windows

The Python examples in [sdk.md](sdk.md) run unchanged. Create the virtual environment with `py -m venv .e2b-venv` and install with `.e2b-venv\Scripts\pip install e2b`.
