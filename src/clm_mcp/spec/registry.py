"""Parses the vendored OpenAPI spec into a compact, queryable operation index.

The spec has no `operationId`s, so each operation is named `{Tag}/{PathTail}`
(e.g. `ShipmentQuery/GetShipmentById`) — the same convention used by
`clm_list_operations` / `clm_describe_operation` / `clm_invoke` (see
`tools/gateway.py`).

All `$ref` pointers in a resolved operation's request/response schema are
inlined eagerly at build time. The schema graph was verified acyclic by hand
during design (343 schemas, all `*Query`/`*Command` DTOs) — `_resolve_ref`
still raises loudly on a cycle rather than assuming that holds forever, since
this spec is fetched from a live, evolving service.

`Test/*` operations are excluded here, at the single choke point every
consumer (curated tools, the gateway, `clm_invoke`) goes through — see
PLAN.md: `Test/GetUserData` leaks super-admin credentials to any
authenticated caller and must never be reachable through this server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

SPEC_PATH: Final = Path(__file__).parent / "swagger.json"

# Operations under these tags are never exposed, regardless of caller intent.
EXCLUDED_TAGS: Final = frozenset({"Test"})

_HTTP_METHODS: Final = frozenset({"get", "post"})
_JSON_CONTENT_TYPES: Final = ("application/json", "text/json", "application/*+json")


class SchemaCycleError(RuntimeError):
    """Raised if the OpenAPI spec's schema graph contains a $ref cycle.

    This should never happen against the spec vendored at build time (it was
    verified acyclic during design), but the spec is refreshed periodically
    from a live upstream service (see `scripts/refresh_spec.py`) and a future
    change introducing a cycle must fail loudly and immediately, not corrupt
    a tool's advertised input schema.
    """


@dataclass(frozen=True, slots=True)
class Operation:
    """One resolved OpenAPI operation, ready to drive both tool schemas and
    the HTTP call `api/client.py` makes to execute it."""

    name: str
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
    method: str
    tag: str
    summary: str


class OperationRegistry:
    """Queryable index of every non-excluded operation in the spec."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._schemas: dict[str, Any] = spec.get("components", {}).get("schemas", {})
        self._operations: dict[str, Operation] = {}
        self._build(spec)

    def _build(self, spec: dict[str, Any]) -> None:
        for path, path_item in spec.get("paths", {}).items():
            for method, op in path_item.items():
                if method not in _HTTP_METHODS:
                    continue
                tags = op.get("tags") or ["Untagged"]
                tag = tags[0]
                if tag in EXCLUDED_TAGS:
                    continue

                name = f"{tag}/{path.rsplit('/', 1)[-1]}"
                summary = " ".join((op.get("summary") or "").split())
                parameters = tuple(op.get("parameters", []))
                request_schema = self._resolve_body_schema(op.get("requestBody", {}))
                response_schema = self._resolve_body_schema(op.get("responses", {}).get("200", {}))

                if name in self._operations:
                    raise ValueError(
                        f"Duplicate operation name {name!r} derived from path {path!r} — "
                        "the Tag/PathTail naming convention no longer disambiguates operations; "
                        "registry.py needs a richer naming scheme."
                    )
                self._operations[name] = Operation(
                    name=name,
                    method=method,
                    path=path,
                    tag=tag,
                    summary=summary,
                    parameters=parameters,
                    request_schema=request_schema,
                    response_schema=response_schema,
                )

    def _resolve_body_schema(self, body_holder: dict[str, Any]) -> dict[str, Any] | None:
        content = body_holder.get("content", {})
        for content_type in _JSON_CONTENT_TYPES:
            if content_type in content:
                schema = content[content_type].get("schema")
                if schema is None:
                    return None
                resolved = self._resolve_ref(schema, frozenset())
                assert isinstance(resolved, dict)  # every JSON Schema body is an object
                return resolved
        return None

    def _resolve_ref(self, node: Any, seen: frozenset[str]) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                if ref_name in seen:
                    raise SchemaCycleError(
                        f"Cyclic $ref detected involving schema {ref_name!r} "
                        f"(path: {' -> '.join((*seen, ref_name))}). The spec was verified "
                        "acyclic at design time; re-run scripts/refresh_spec.py to inspect "
                        "what changed upstream before proceeding."
                    )
                if ref_name not in self._schemas:
                    raise KeyError(f"Schema {ref_name!r} referenced but not defined in the spec")
                resolved = self._resolve_ref(self._schemas[ref_name], seen | {ref_name})
                if isinstance(resolved, dict) and "title" not in resolved:
                    return {**resolved, "title": ref_name}
                return resolved
            return {k: self._resolve_ref(v, seen) for k, v in node.items()}
        if isinstance(node, list):
            return [self._resolve_ref(v, seen) for v in node]
        return node

    def get(self, name: str) -> Operation | None:
        """Look up a full, schema-resolved operation by name, or None."""
        return self._operations.get(name)

    def list_operations(
        self, *, tag: str | None = None, search: str | None = None
    ) -> list[OperationSummary]:
        """List operations (name/method/tag/summary only), optionally
        filtered by exact tag and/or a case-insensitive substring match
        against the name or summary."""
        needle = search.lower() if search else None
        results = []
        for op in self._operations.values():
            if tag is not None and op.tag != tag:
                continue
            if needle is not None:
                haystack = f"{op.name} {op.summary}".lower()
                if needle not in haystack:
                    continue
            results.append(
                OperationSummary(name=op.name, method=op.method, tag=op.tag, summary=op.summary)
            )
        return sorted(results, key=lambda o: o.name)

    def tags(self) -> list[str]:
        return sorted({op.tag for op in self._operations.values()})

    def names(self) -> set[str]:
        """All registered operation names (used by `scripts/refresh_spec.py`
        to diff two spec versions without duplicating name derivation)."""
        return set(self._operations)

    def __len__(self) -> int:
        return len(self._operations)


def load_registry(spec_path: Path = SPEC_PATH) -> OperationRegistry:
    """Parse the spec at `spec_path` into a fresh OperationRegistry.

    Exposed separately from `get_registry()` so tests and
    `scripts/refresh_spec.py` can load an arbitrary spec file without going
    through the process-wide cache.
    """
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    return OperationRegistry(spec)


@lru_cache(maxsize=1)
def get_registry() -> OperationRegistry:
    """Return the process-wide OperationRegistry, built once from the
    vendored spec on first access."""
    return load_registry()
