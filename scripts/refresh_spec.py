#!/usr/bin/env python3
"""Re-download the ClmShipmentWebService OpenAPI spec and report drift.

Compares the currently vendored spec (`src/clm_mcp/spec/swagger.json`)
against a freshly downloaded one, reporting any added or removed
operations. Both specs are parsed through the same `OperationRegistry` used
in production, so a spec that introduces a schema `$ref` cycle is caught
here (as a `SchemaCycleError`) rather than surfacing later as a broken tool.

Usage:
    uv run python scripts/refresh_spec.py            # dry run: report only
    uv run python scripts/refresh_spec.py --write     # also update the vendored file

A dry run never touches the vendored spec — review the reported diff (and,
for any removed operation, whether a curated tool in `src/clm_mcp/tools/`
depends on it) before re-running with `--write`.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from clm_mcp.spec.registry import SPEC_PATH, OperationRegistry, SchemaCycleError

SPEC_URL = "https://msblocks.selisestage.com/api/business-clm-shipment/swagger/v1/swagger.json"


def fetch_spec(url: str) -> dict[str, object]:
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    result: dict[str, object] = response.json()
    return result


def report_diff(old_names: set[str], new_names: set[str]) -> None:
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)

    if not added and not removed:
        print("No operation changes detected.")
        return

    if added:
        print(f"Added operations ({len(added)}):")
        for name in added:
            print(f"  + {name}")
    if removed:
        print(f"Removed operations ({len(removed)}):")
        for name in removed:
            print(f"  - {name}")
        print()
        print(
            "A removed operation that a curated tool in src/clm_mcp/tools/ depends on\n"
            "will fail at call time, not at import time — grep for it there before\n"
            "applying this refresh with --write."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Overwrite the vendored spec after reporting"
    )
    args = parser.parse_args()

    print(f"Fetching {SPEC_URL} ...")
    new_spec = fetch_spec(SPEC_URL)

    try:
        new_registry = OperationRegistry(new_spec)
    except SchemaCycleError as exc:
        print(f"ERROR: the fetched spec has a cyclic schema reference: {exc}", file=sys.stderr)
        return 1

    old_registry = OperationRegistry(json.loads(SPEC_PATH.read_text(encoding="utf-8")))
    report_diff(old_registry.names(), new_registry.names())

    if args.write:
        SPEC_PATH.write_text(json.dumps(new_spec, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote refreshed spec to {SPEC_PATH}")
    else:
        print("\nDry run — pass --write to update the vendored spec.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
