"""Parses the vendored OpenAPI specs into one compact, queryable operation
index spanning all four CLM services (see `services_catalog.py`).

None of the specs have `operationId`s, so each operation's canonical name is
`{service}/{Tag}/{PathTail}` (e.g. `shipment/ShipmentQuery/GetShipmentById`).
Before this module supported multiple services, the name was just
`{Tag}/{PathTail}` — verified unique across all 364 usable operations in all
four specs combined (see PLAN.md "Name-collision analysis"), so that shorter
form is still accepted everywhere a canonical name is (`OperationRegistry
.resolve`, and therefore `clm_invoke` / `clm_describe_operation` / every
curated service module's `get_operation` call), for backwards compatibility
with every pre-multi-service caller.

All `$ref` pointers in a resolved operation's request/response schema are
inlined eagerly at build time, per-service (a `$ref` never crosses spec
documents). The shipment spec's schema graph is acyclic (343 schemas, all
`*Query`/`*Command` DTOs), but the construction and konshub specs are not —
`KonsHubShipment` self-references via `PreviousKonsHubShipments`, and
construction's `Reservation`/`ReservationObject`/`ReservationObjectSpecificDate`
form a mutual cycle (see PLAN.md "Verified facts"). `_resolve_ref` breaks a
cycle with an untyped `{"type": "object"}` placeholder rather than raising,
so those four operations stay fully describable and invocable — a nested
self-referencing field just loses static pre-validation at the point the
cycle closes, which is no worse than any other deeply-nested field's
`dict[str, Any]` fallback (see `tools/commands.py`).

`Test/*` operations are excluded here, at the single choke point every
consumer (curated tools, the gateway, `clm_invoke`) goes through, for all
four services — see PLAN.md: `Test/GetUserData` leaks super-admin
credentials to any authenticated caller in every one of them and must never
be reachable through this server.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from clm_mcp.services_catalog import SERVICES

SPEC_DIR: Final = Path(__file__).parent / "specs"

# Operations under these tags are never exposed, regardless of caller intent.
EXCLUDED_TAGS: Final = frozenset({"Test"})

_HTTP_METHODS: Final = frozenset({"get", "post"})
_JSON_CONTENT_TYPES: Final = ("application/json", "text/json", "application/*+json")

# Belt-and-braces guard against a pathological future spec: a legitimate
# resolution never gets close to this (the deepest known chain is a handful
# of levels), so hitting it means something is degenerate, not that a cycle
# was merely missed by the seen-set check below.
_MAX_RESOLUTION_DEPTH: Final = 40


class SchemaCycleError(RuntimeError):
    """Kept for backwards compatibility with `scripts/refresh_spec.py` and
    any external caller that still imports it; the resolver itself no longer
    raises it — see `_resolve_ref`, which breaks a cycle with a placeholder
    instead of failing the whole registry build over four operations in two
    of the four vendored services.
    """


class AmbiguousOperationError(LookupError):
    """Raised by `OperationRegistry.resolve` when an unqualified
    `{Tag}/{PathTail}` name matches more than one service's operation.

    Not currently reachable: verified during design that all 364 usable
    operations across all four services have unique `{Tag}/{PathTail}`
    tails. Exists so a future spec change that breaks that invariant fails
    with a clear, actionable message instead of silently picking one
    service's operation over another's.
    """

    def __init__(self, name: str, candidates: list[str]) -> None:
        self.name = name
        self.candidates = candidates
        super().__init__(
            f"{name!r} is ambiguous across services: {candidates}. "
            "Use the fully qualified '{service}/{tag}/{operation}' form."
        )


@dataclass(frozen=True, slots=True)
class Operation:
    """One resolved OpenAPI operation, ready to drive both tool schemas and
    the HTTP call `api/client.py` makes to execute it."""

    name: str
    service: str
    method: str
    path: str
    tag: str
    summary: str
    parameters: tuple[dict[str, Any], ...]
    request_schema: dict[str, Any] | None
    response_schema: dict[str, Any] | None

    @property
    def is_query(self) -> bool:
        return self.tag.endswith("Query")

    @property
    def is_command(self) -> bool:
        return self.tag.endswith("Command")


@dataclass(frozen=True, slots=True)
class OperationSummary:
    """A lightweight projection of `Operation`, for `clm_list_operations` —
    deliberately excludes the (potentially large) resolved schemas."""

    name: str
    service: str
    method: str
    tag: str
    summary: str


class OperationRegistry:
    """Queryable index of every non-excluded operation across all services
    passed to it."""

    def __init__(self, specs: Mapping[str, dict[str, Any]]) -> None:
        # Per-service schema dicts, not merged: a `$ref` only ever resolves
        # within its own spec document, and two services can (and do) reuse
        # a DTO name (e.g. every service has its own `CommandResponse`).
        self._schemas: dict[str, dict[str, Any]] = {
            slug: spec.get("components", {}).get("schemas", {}) for slug, spec in specs.items()
        }
        self._operations: dict[str, Operation] = {}
        # unqualified "{Tag}/{PathTail}" -> [canonical names] sharing it,
        # for OperationRegistry.resolve's backwards-compatible lookup.
        self._unqualified_index: dict[str, list[str]] = defaultdict(list)
        for slug, spec in specs.items():
            self._build_service(slug, spec)

    def _build_service(self, slug: str, spec: dict[str, Any]) -> None:
        service = SERVICES.get(slug)
        path_prefix = service.path_prefix if service is not None else None

        for path, path_item in spec.get("paths", {}).items():
            if path_prefix is not None and not path.startswith(path_prefix + "/"):
                raise ValueError(
                    f"{slug}: operation path {path!r} does not start with the expected "
                    f"prefix {path_prefix!r} for this service — services_catalog.py and "
                    "the vendored spec have drifted apart."
                )

            for method, op in path_item.items():
                if method not in _HTTP_METHODS:
                    continue
                tags = op.get("tags") or ["Untagged"]
                tag = tags[0]
                if tag in EXCLUDED_TAGS:
                    continue

                path_tail = path.rsplit("/", 1)[-1]
                unqualified_name = f"{tag}/{path_tail}"
                name = f"{slug}/{unqualified_name}"
                summary = " ".join((op.get("summary") or "").split())
                parameters = tuple(op.get("parameters", []))
                request_schema = self._resolve_body_schema(slug, op.get("requestBody", {}))
                response_schema = self._resolve_body_schema(
                    slug, op.get("responses", {}).get("200", {})
                )

                if name in self._operations:
                    raise ValueError(
                        f"Duplicate operation name {name!r} derived from path {path!r} within "
                        f"service {slug!r} — the Tag/PathTail naming convention no longer "
                        "disambiguates operations within this service; registry.py needs a "
                        "richer naming scheme."
                    )
                self._operations[name] = Operation(
                    name=name,
                    service=slug,
                    method=method,
                    path=path,
                    tag=tag,
                    summary=summary,
                    parameters=parameters,
                    request_schema=request_schema,
                    response_schema=response_schema,
                )
                self._unqualified_index[unqualified_name].append(name)

    def _resolve_body_schema(self, slug: str, body_holder: dict[str, Any]) -> dict[str, Any] | None:
        content = body_holder.get("content", {})
        for content_type in _JSON_CONTENT_TYPES:
            if content_type in content:
                schema = content[content_type].get("schema")
                if schema is None:
                    return None
                resolved = self._resolve_ref(slug, schema, frozenset(), 0)
                assert isinstance(resolved, dict)  # every JSON Schema body is an object
                return resolved
        return None

    def _resolve_ref(self, slug: str, node: Any, seen: frozenset[str], depth: int) -> Any:
        if depth > _MAX_RESOLUTION_DEPTH:
            return {
                "type": "object",
                "description": "<schema nesting depth-capped; use clm_describe_operation "
                "for the full shape>",
            }
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                if ref_name in seen:
                    # A genuine cycle (e.g. KonsHubShipment self-referencing via
                    # PreviousKonsHubShipments, or construction's Reservation <->
                    # ReservationObject) — break it with a permissive placeholder
                    # rather than raising. `{"type": "object"}` still validates
                    # correctly under jsonschema (tools/gateway.py::_validate_params)
                    # and synthesizes a valid dict[str, Any] field
                    # (tools/commands.py::_python_type_for_schema); the API itself
                    # still validates the real nested shape at call time.
                    return {
                        "type": "object",
                        "title": ref_name,
                        "description": f"<recursive reference to {ref_name}; use "
                        "clm_describe_operation for its shape>",
                    }
                schemas = self._schemas[slug]
                if ref_name not in schemas:
                    raise KeyError(
                        f"{slug}: schema {ref_name!r} referenced but not defined in the spec"
                    )
                resolved = self._resolve_ref(slug, schemas[ref_name], seen | {ref_name}, depth + 1)
                if isinstance(resolved, dict) and "title" not in resolved:
                    return {**resolved, "title": ref_name}
                return resolved
            return {k: self._resolve_ref(slug, v, seen, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [self._resolve_ref(slug, v, seen, depth + 1) for v in node]
        return node

    def get(self, name: str) -> Operation | None:
        """Exact canonical-key lookup (`{service}/{Tag}/{PathTail}`) only.
        Use `resolve` to also accept the unqualified `{Tag}/{PathTail}` form."""
        return self._operations.get(name)

    def resolve(self, name: str) -> Operation | None:
        """Look up an operation by its canonical `{service}/{Tag}/{PathTail}`
        name, or by the unqualified `{Tag}/{PathTail}` form when that's
        unique across all loaded services (true for all 364 usable
        operations as of this spec — see PLAN.md). Raises
        `AmbiguousOperationError` if it isn't.
        """
        direct = self._operations.get(name)
        if direct is not None:
            return direct
        candidates = self._unqualified_index.get(name)
        if not candidates:
            return None
        if len(candidates) > 1:
            raise AmbiguousOperationError(name, sorted(candidates))
        return self._operations[candidates[0]]

    def list_operations(
        self,
        *,
        service: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> list[OperationSummary]:
        """List operations (name/service/method/tag/summary only), optionally
        filtered by exact `service`, exact `tag`, and/or a case-insensitive
        substring match against the name or summary."""
        needle = search.lower() if search else None
        results = []
        for op in self._operations.values():
            if service is not None and op.service != service:
                continue
            if tag is not None and op.tag != tag:
                continue
            if needle is not None:
                haystack = f"{op.name} {op.summary}".lower()
                if needle not in haystack:
                    continue
            results.append(
                OperationSummary(
                    name=op.name,
                    service=op.service,
                    method=op.method,
                    tag=op.tag,
                    summary=op.summary,
                )
            )
        return sorted(results, key=lambda o: o.name)

    def tags(self, *, service: str | None = None) -> list[str]:
        return sorted(
            {op.tag for op in self._operations.values() if service is None or op.service == service}
        )

    def names(self) -> set[str]:
        """All registered canonical operation names (used by
        `scripts/refresh_spec.py` to diff two spec versions without
        duplicating name derivation)."""
        return set(self._operations)

    def __len__(self) -> int:
        return len(self._operations)


def load_registry(spec_dir: Path = SPEC_DIR) -> OperationRegistry:
    """Parse every vendored spec under `spec_dir` into a fresh, merged
    OperationRegistry.

    Exposed separately from `get_registry()` so tests and
    `scripts/refresh_spec.py` can load arbitrary spec files without going
    through the process-wide cache.
    """
    specs = {
        slug: json.loads((spec_dir / service.spec_filename).read_text(encoding="utf-8"))
        for slug, service in SERVICES.items()
    }
    return OperationRegistry(specs)


@lru_cache(maxsize=1)
def get_registry() -> OperationRegistry:
    """Return the process-wide OperationRegistry, built once from the
    vendored specs (all four services) on first access."""
    return load_registry()
