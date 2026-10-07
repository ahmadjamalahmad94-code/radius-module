"""Minimal, dependency-free JSON-Schema (2020-12 subset) validator for the
operations-assistant catalog (``catalog_ops_v1.json``).

Only the keywords the catalog actually uses are implemented — an unknown
keyword is a programming error and raises (fail closed), so a catalog update
that needs a new keyword cannot silently weaken validation:

  type, enum, const, pattern, minLength, maxLength, minimum, maximum,
  exclusiveMinimum, properties, required, additionalProperties (bool),
  items, uniqueItems, minItems, maxItems, maxProperties, allOf, anyOf,
  oneOf, not, if/then, $ref (local ``#/$defs/...``).

Annotation keywords ($schema, $id, title, description, $defs) are ignored.
Returns a list of ``(path, message)`` violations (empty = valid).
"""
from __future__ import annotations

import math
import re
from typing import Any

_ANNOTATIONS = frozenset({"$schema", "$id", "title", "description", "$defs", "default"})
_KNOWN = frozenset({
    "type", "enum", "const", "pattern", "minLength", "maxLength", "minimum",
    "maximum", "exclusiveMinimum", "properties", "required",
    "additionalProperties", "items", "uniqueItems", "minItems", "maxItems",
    "maxProperties", "allOf", "anyOf", "oneOf", "not", "if", "then", "$ref",
}) | _ANNOTATIONS

_PATTERN_CACHE: dict[str, re.Pattern] = {}


class SchemaError(RuntimeError):
    """The schema itself uses an unsupported keyword (programming error)."""


def _is_type(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        # JSON has no NaN / Infinity (Python's json accepts them): NaN passes
        # every comparison (NaN < 0, NaN > max, selling < wholesale are False)
        if isinstance(value, float):
            return math.isfinite(value)
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "null":
        return value is None
    raise SchemaError(f"unsupported type {t!r}")


def _eq(a: Any, b: Any) -> bool:
    # JSON equality: True != 1 (bool is not a number here)
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _ecma_pattern(pattern: str) -> str:
    r"""JSON-Schema patterns are ECMA-262: ``$`` matches only at the very end.
    Python's ``$`` also matches BEFORE a trailing newline, so
    ``^[A-Za-z0-9._@-]{1,64}$`` accepted ``"bob\n"``. Every unescaped ``$``
    outside a character class becomes ``\Z``."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "$":
            out.append(r"\Z")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _resolve(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise SchemaError(f"only local $ref supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate(instance: Any, schema: dict, root: dict | None = None,
             path: str = "$") -> list[tuple[str, str]]:
    root = root if root is not None else schema
    errs: list[tuple[str, str]] = []
    if schema is True or schema == {}:
        return errs
    if schema is False:
        return [(path, "not allowed")]
    unknown = set(schema) - _KNOWN
    if unknown:
        raise SchemaError(f"unsupported keywords {sorted(unknown)} at {path}")

    if "$ref" in schema:
        errs += validate(instance, _resolve(root, schema["$ref"]), root, path)

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            return errs + [(path, f"expected {'/'.join(types)}")]
    if "enum" in schema and not any(_eq(instance, v) for v in schema["enum"]):
        errs.append((path, f"not one of {schema['enum']}"))
    if "const" in schema and not _eq(instance, schema["const"]):
        errs.append((path, f"must equal {schema['const']!r}"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errs.append((path, f"shorter than {schema['minLength']}"))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errs.append((path, f"longer than {schema['maxLength']}"))
        if "pattern" in schema:
            pat = _PATTERN_CACHE.get(schema["pattern"])
            if pat is None:
                pat = _PATTERN_CACHE.setdefault(
                    schema["pattern"], re.compile(_ecma_pattern(schema["pattern"])))
            if not pat.search(instance):
                errs.append((path, "does not match the required pattern"))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append((path, f"less than {schema['minimum']}"))
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append((path, f"greater than {schema['maximum']}"))
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errs.append((path, f"must be greater than {schema['exclusiveMinimum']}"))

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errs.append((f"{path}.{key}", "is required"))
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errs.append((path, f"more than {schema['maxProperties']} properties"))
        for key, val in instance.items():
            if key in props:
                errs += validate(val, props[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                errs.append((f"{path}.{key}", "unknown field"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append((path, f"fewer than {schema['minItems']} items"))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errs.append((path, f"more than {schema['maxItems']} items"))
        if schema.get("uniqueItems"):
            seen: list = []
            for item in instance:
                if any(_eq(item, s) for s in seen):
                    errs.append((path, "items are not unique"))
                    break
                seen.append(item)
        if "items" in schema:
            for i, item in enumerate(instance):
                errs += validate(item, schema["items"], root, f"{path}[{i}]")

    for sub in schema.get("allOf", []):
        errs += validate(instance, sub, root, path)
    if "anyOf" in schema:
        if not any(not validate(instance, s, root, path) for s in schema["anyOf"]):
            errs.append((path, "matches none of the allowed shapes (anyOf)"))
    if "oneOf" in schema:
        n = sum(1 for s in schema["oneOf"] if not validate(instance, s, root, path))
        if n != 1:
            errs.append((path, "must match exactly one allowed shape (oneOf)"))
    if "not" in schema and not validate(instance, schema["not"], root, path):
        errs.append((path, "matches a forbidden combination (not)"))
    if "if" in schema:
        if not validate(instance, schema["if"], root, path):
            if "then" in schema:
                errs += validate(instance, schema["then"], root, path)
    return errs


__all__ = ["validate", "SchemaError"]
