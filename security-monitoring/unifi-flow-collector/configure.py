#!/usr/bin/env python3
"""Create a local UniFi flow configuration without contacting either endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from tempfile import NamedTemporaryFile

from unifi_flow_collector import parse_config

EXAMPLE = Path(__file__).with_name("config.example.toml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=EXAMPLE.with_name("config.local.toml")
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="explicitly replace an existing config"
    )
    parser.add_argument("--controller-url")
    parser.add_argument("--site")
    parser.add_argument("--api-key-env")
    parser.add_argument("--hec-url")
    parser.add_argument("--hec-token-env")
    parser.add_argument("--index")
    parser.add_argument("--state-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    temporary = None
    try:
        if os.path.lexists(args.output) and not args.overwrite:
            raise ValueError(f"refusing to replace existing file: {args.output}")
        template = EXAMPLE.read_text(encoding="utf-8")
        payload = tomllib.loads(template)
        for section, key, value in (
            ("unifi", "controller_url", args.controller_url),
            ("unifi", "site", args.site),
            ("unifi", "api_key_env", args.api_key_env),
            ("hec", "url", args.hec_url),
            ("hec", "token_env", args.hec_token_env),
            ("hec", "index", args.index),
            ("collector", "state_path", args.state_file),
        ):
            if value is not None:
                payload[section][key] = value
        parse_config(payload, args.output.parent)
        lines = []
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
        if args.overwrite:
            os.replace(temporary, args.output)
        else:
            # Linking a complete file is atomic and refuses a concurrent creator.
            os.link(temporary, args.output)
    except (OSError, ValueError) as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"configuration-written: {args.output}")
    print(
        "next-step: review CUSTOMIZE values, set credentials, then preview with --once"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
