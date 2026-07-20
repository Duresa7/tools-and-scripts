# Prometheus target check

`check_targets.py` compares the live Prometheus active-target response with a JSON expectation file. It detects missing targets, unexpected targets, duplicate active targets, stale address fragments, and targets outside the required health state.

## Usage

Pipe a local Prometheus response into the checker:

```bash
curl -fsS http://127.0.0.1:9090/api/v1/targets \
  | python prometheus/check_targets.py \
      --expect prometheus/expected-targets.example.json
```

The script can fetch the API itself:

```bash
python prometheus/check_targets.py \
  --url http://127.0.0.1:9090/api/v1/targets \
  --expect prometheus/expected-targets.example.json
```

Copy `expected-targets.example.json` and replace the documentation addresses with the exact jobs and scrape URLs from your Prometheus configuration. Repeated job names are supported when their scrape URLs differ.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | The exact target set is present and every target has the required health value |
| `1` | Input, JSON, HTTP, or expectation parsing failed |
| `2` | One or more target assertions failed |

The output prints one pipe-delimited row per target so a runbook or CI log retains the observed job, health, URL, and last error.
