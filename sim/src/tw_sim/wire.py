"""Tagged-union serialization shared by `command` and `event`.

Both are closed sets of frozen dataclasses discriminated by a `kind` string.
The wire form is that tag plus a flat payload of plain ints and bools —
`{"kind": "city_fell", "province": 3, "from": 1, "to": 0}` — which mirrors the
`event_to_dict` the Godot client already used, so we know the shape covers every
variant.

Enums travel as their integer values rather than strings.

One wrinkle: `from` is a Python keyword, so the dataclasses spell those fields
`from_`. A trailing underscore is stripped on the way out and restored on the
way in, keeping the wire name the obvious one.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import IntEnum
from functools import lru_cache
from typing import Any, ClassVar, Protocol, TypeVar, get_type_hints


class Tagged(Protocol):
    kind: ClassVar[str]


T = TypeVar("T")


def wire_name(name: str) -> str:
    return name[:-1] if name.endswith("_") else name


def to_dict(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": obj.kind}
    for f in fields(obj):
        v = getattr(obj, f.name)
        out[wire_name(f.name)] = int(v) if isinstance(v, IntEnum) else v
    return out


def registry(*classes: type[T]) -> dict[str, type[T]]:
    """Index a set of variants by their `kind` tag, rejecting duplicates."""
    out: dict[str, type[T]] = {}
    for c in classes:
        assert is_dataclass(c), c
        if c.kind in out:
            raise ValueError(f"duplicate wire kind {c.kind!r}")
        out[c.kind] = c
    return out


@lru_cache(maxsize=None)
def _enum_fields(cls: type) -> dict[str, type[IntEnum]]:
    """Which fields need coercing back from `int` on decode. `from __future__
    import annotations` leaves `field.type` a string, so resolve it properly."""
    hints = get_type_hints(cls)
    return {
        f.name: hints[f.name]
        for f in fields(cls)
        if isinstance(hints.get(f.name), type) and issubclass(hints[f.name], IntEnum)
    }


def from_dict(table: dict[str, type[T]], raw: dict[str, Any]) -> T:
    """Rebuild a variant from its wire form, coercing enum fields back.

    Unknown tags and missing/extra keys raise — a frontend sending something we
    do not understand is a bug to surface, not to silently drop.
    """
    try:
        cls = table[raw["kind"]]
    except KeyError:
        raise ValueError(f"unknown wire kind {raw.get('kind')!r}") from None
    enums = _enum_fields(cls)
    kwargs = {}
    for f in fields(cls):
        key = wire_name(f.name)
        if key not in raw:
            raise ValueError(f"{raw['kind']}: missing field {key!r}")
        v = raw[key]
        kwargs[f.name] = enums[f.name](v) if f.name in enums else v
    return cls(**kwargs)
