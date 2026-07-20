# Prometheus target check

`check_targets.py` compares the live Prometheus active-target response with a JSON expectation file. It detects missing targets, unexpected targets, duplicate active targets, stale address fragments, and targets outside the required health state.

## What you must customize

Copy `expected-targets.example.json` to `expected-targets.json` and follow its `_comment` fields. Replace the job names and scrape URLs with the exact values shown by your Prometheus `/api/v1/targets` endpoint. Add every target you expect, remove the examples, list any retired address fragments under `forbidden_substrings`, and normally leave `required_health` set to `up`. You do not need to edit `check_targets.py`.

```bash
cp monitoring/prometheus-target-check/expected-targets.example.json monitoring/prometheus-target-check/expected-targets.json
```

## Usage

Pipe a local Prometheus response into the checker:

```bash
curl -fsS http://127.0.0.1:9090/api/v1/targets \
  | python monitoring/prometheus-target-check/check_targets.py \
      --expect monitoring/prometheus-target-check/expected-targets.json
```

The script can fetch the API itself:

```bash
python monitoring/prometheus-target-check/check_targets.py \
  --url http://127.0.0.1:9090/api/v1/targets \
  --expect monitoring/prometheus-target-check/expected-targets.json
```

Repeated job names are supported when their scrape URLs differ.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | The exact target set is present and every target has the required health value |
| `1` | Input, JSON, HTTP, or expectation parsing failed |
| `2` | One or more target assertions failed |

The output prints one pipe-delimited row per target so a runbook or captured command log retains the observed job, health, URL, and last error.
