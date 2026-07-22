"""Opaque handles for the three things the world is made of.

These are `NewType`s over `int` rather than classes: they cost nothing at
runtime, index straight into `GameState.factions` / `.provinces`, and still make
`FactionId` and `ProvinceId` distinct to a type checker.
"""

from typing import NewType

FactionId = NewType("FactionId", int)
ProvinceId = NewType("ProvinceId", int)
ArmyId = NewType("ArmyId", int)


def army_label(id: ArmyId) -> str:
    """How an army id reads in prose — matches the Rust `Display` impl."""
    return f"#{id}"
