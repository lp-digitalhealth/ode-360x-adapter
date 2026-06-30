"""Plugin registry — selects port implementations by name at runtime.

Built-in plugins register themselves on import (see plugins/__init__.py). Third
parties can ship their own by registering under the same kinds, or via Python
entry points in the group "ode_adapter.plugins" (loaded lazily below).

    from ode_adapter import registry
    backend = registry.create("fhir", "generic-r4", dry_run=True)
    print(registry.available("fhir"))   # -> ['generic-r4', 'onyx', ...]
"""
from __future__ import annotations

from typing import Type

KINDS = ("fhir", "codec", "transport")
_REGISTRY: dict[str, dict[str, type]] = {k: {} for k in KINDS}
_ENTRY_POINTS_LOADED = False


def register(kind: str, name: str):
    """Class decorator: register a plugin under (kind, name)."""
    if kind not in KINDS:
        raise ValueError(f"unknown plugin kind: {kind}")

    def deco(cls: Type) -> Type:
        cls.name = name
        _REGISTRY[kind][name] = cls
        return cls

    return deco


def _load_entry_points() -> None:
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="ode_adapter.plugins"):
            ep.load()  # the plugin module registers itself on import
    except Exception:
        pass  # entry points are optional


def get(kind: str, name: str) -> type:
    _load_entry_points()
    try:
        return _REGISTRY[kind][name]
    except KeyError:
        raise KeyError(
            f"no '{kind}' plugin named '{name}'. Available: {available(kind)}")


def create(kind: str, name: str, **kwargs):
    return get(kind, name)(**kwargs)


def available(kind: str) -> list[str]:
    _load_entry_points()
    return sorted(_REGISTRY[kind])


def all_plugins() -> dict[str, list[str]]:
    return {k: available(k) for k in KINDS}
