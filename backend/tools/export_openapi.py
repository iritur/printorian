"""Export the OpenAPI schema.

This file is the contract between the backend and both clients. The TypeScript
client is generated from it and never hand-written (ADR-0005) — which is what
structurally prevents V1's failure, where the desktop and the web each grew their
own idea of the domain and silently diverged.

Usage::

    python tools/export_openapi.py --out openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from printorian.api.app import create_app
from printorian.core.config import Environment, Settings


def build_schema() -> dict[str, object]:
    app = create_app(Settings(environment=Environment.TEST))
    schema: dict[str, object] = app.openapi()
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("openapi.json"))
    args = parser.parse_args()

    schema = build_schema()
    args.out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    paths = schema.get("paths", {})
    count = len(paths) if isinstance(paths, dict) else 0
    print(f"wrote {args.out} ({count} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
