#!/usr/bin/env python3
"""Create a local configuration for UniFi flow dashboards without contacting Splunk."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from tempfile import NamedTemporaryFile

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from unifi_flow_dashboards import parse_config

EXAMPLE = Path(__file__).with_name("config.example.toml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=EXAMPLE.with_name("config.local.toml")
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="explicitly replace an existing config",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="alias for --overwrite",
    )
    parser.add_argument("--splunk-url")
    parser.add_argument("--app")
    parser.add_argument("--auth-token-env")
    parser.add_argument("--username")
    parser.add_argument("--password-env")
    parser.add_argument("--output-dir")
    parser.add_argument("--flow-index")
    parser.add_argument("--flow-sourcetype")
    parser.add_argument("--event-index")
    parser.add_argument("--event-sourcetype")
    parser.add_argument("--external-url")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    temporary = None
    allow_overwrite = args.overwrite or args.force

    try:
        if os.path.lexists(args.output) and not allow_overwrite:
            raise ValueError(f"refusing to replace existing file: {args.output}")

        template = EXAMPLE.read_text(encoding="utf-8")
        payload = tomllib.loads(template)

        for section, key, value in (
            ("splunk", "url", args.splunk_url),
            ("splunk", "app", args.app),
            ("splunk", "auth_token_env", args.auth_token_env),
            ("splunk", "username", args.username),
            ("splunk", "password_env", args.password_env),
            ("dashboards", "output_dir", args.output_dir),
            ("dashboards", "flow_index", args.flow_index),
            ("dashboards", "flow_sourcetype", args.flow_sourcetype),
            ("dashboards", "event_index", args.event_index),
            ("dashboards", "event_sourcetype", args.event_sourcetype),
            ("dashboards", "external_url", args.external_url),
        ):
            if value is not None:
                payload[section][key] = value

        parse_config(payload, args.output.parent)

        lines: list[str] = []
        section = ""
        for line in template.splitlines():
            if line.startswith("["):
                section = line.strip("[]")
            elif "=" in line and not line.startswith("#"):
                key = line.split("=", 1)[0].strip()
                line = (
                    f"{key} = {json.dumps(payload[section][key], ensure_ascii=False)}"
                )
            lines.append(line)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        if allow_overwrite:
            os.replace(temporary, args.output)
        else:
            os.link(temporary, args.output)
    except (OSError, ValueError) as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    print(f"configuration-written: {args.output}")
    print(
        "next-step: review CUSTOMIZE values, set credentials, and run build or verify"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
