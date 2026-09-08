#!/usr/bin/env python3
"""Re-download CLM OpenAPI specs and report drift.

Compares each currently vendored spec (`src/clm_mcp/spec/specs/{slug}.json`)
against a freshly downloaded one, reporting any added or removed operations.
Every fetched spec is parsed through the same `OperationRegistry` used in
production, so a spec that introduces a schema `$ref` cycle beyond what the
resolver can already break — or any other structural problem — is caught
here rather than surfacing later as a broken tool.

Usage:
    uv run python scripts/refresh_spec.py                    # dry run, all 4 services
    uv run python scripts/refresh_spec.py --write             # write all 4
    uv run python scripts/refresh_spec.py --service konshub   # just one service
    uv run python scripts/refresh_spec.py --service konshub --write

A dry run never touches a vendored spec — review the reported diff (and, for
any removed operation, whether a curated tool in `src/clm_mcp/tools/` depends
on it) before re-running with `--write`.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from clm_mcp.services_catalog import SERVICES, ClmService
from clm_mcp.spec.registry import SPEC_DIR, OperationRegistry

SPEC_URL_TEMPLATE = "https://msblocks.selisestage.com/api/{gateway_segment}/swagger/v1/swagger.json"


def fetch_spec(url: str) -> dict[str, object]:
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    result: dict[str, object] = response.json()
    return result


def report_diff(service_slug: str, old_names: set[str], new_names: set[str]) -> None:
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)

    if not added and not removed:
        print(f"[{service_slug}] No operation changes detected.")
        return

    if added:
        print(f"[{service_slug}] Added operations ({len(added)}):")
        for name in added:
            print(f"  + {name}")
    if removed:
        print(f"[{service_slug}] Removed operations ({len(removed)}):")
        for name in removed:
            print(f"  - {name}")
        print()
        print(
            f"A removed {service_slug} operation that a curated tool in src/clm_mcp/tools/\n"
            "depends on will fail at call time, not at import time — grep for it there\n"
            "before applying this refresh with --write."
        )


def refresh_one(service: ClmService, *, write: bool) -> int:
    url = SPEC_URL_TEMPLATE.format(gateway_segment=service.gateway_segment)
    print(f"Fetching {url} ...")
    new_spec = fetch_spec(url)

    spec_path = SPEC_DIR / service.spec_filename
    try:
        # Validate the fetched spec in isolation (as the sole member of a
        # single-service registry) so a structural problem is attributed to
        # this one service, not conflated with the other three.
        OperationRegistry({service.slug: new_spec})
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        print(f"ERROR: [{service.slug}] spec failed to parse: {exc}", file=sys.stderr)
        return 1

    old_spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.is_file() else None
    if old_spec is not None:
        old_registry = OperationRegistry({service.slug: old_spec})
        new_registry = OperationRegistry({service.slug: new_spec})
        report_diff(service.slug, old_registry.names(), new_registry.names())
    else:
        print(f"[{service.slug}] No previously vendored spec at {spec_path} — nothing to diff.")

    if write:
        spec_path.write_text(json.dumps(new_spec, indent=2) + "\n", encoding="utf-8")
        print(f"[{service.slug}] Wrote refreshed spec to {spec_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Overwrite the vendored spec(s) after reporting"
    )
    parser.add_argument(
        "--service",
        choices=sorted(SERVICES),
        default=None,
        help="Refresh only this service (default: all four)",
    )
    args = parser.parse_args()

    targets = [SERVICES[args.service]] if args.service else list(SERVICES.values())

    exit_code = 0
    for service in targets:
        exit_code = max(exit_code, refresh_one(service, write=args.write))
        print()

    if not args.write:
        print("Dry run — pass --write to update the vendored spec(s).")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
