"""Local printer credentials for the Phase 0 spike.

**This is spike tooling, not the product.** From Phase 3 the fleet context owns
printers as database records with their access codes encrypted at rest (ADR-0014).
This file exists so that bringing a second, fifth or twentieth printer onto the
bench does not mean retyping a serial and an access code every time.

Credentials live in ``backend/printers.local.toml``, which is git-ignored. Format::

    [printers.p1s-01]
    host = "192.168.0.180"
    serial = "20P6BJ632700731"
    access_code = "03d00058"
    model = "P1S"          # optional, for your own reference
    notes = "bench unit"   # optional

Every field can still be overridden on the command line, and a printer that is not
in the file can be driven entirely from flags.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "printers.local.toml"


@dataclass(frozen=True, slots=True)
class PrinterCredentials:
    name: str
    host: str
    serial: str
    access_code: str
    model: str = ""
    notes: str = ""


class RegistryError(RuntimeError):
    """The registry is missing, malformed, or lacks the requested printer."""


def load(path: Path | None = None) -> dict[str, PrinterCredentials]:
    """Read the registry. An absent file is not an error — it means "no entries"."""
    location = path or REGISTRY_PATH
    if not location.is_file():
        return {}

    try:
        raw = tomllib.loads(location.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"{location} is not valid TOML: {exc}") from exc

    entries: dict[str, PrinterCredentials] = {}
    for name, body in (raw.get("printers") or {}).items():
        missing = [key for key in ("host", "serial", "access_code") if not body.get(key)]
        if missing:
            raise RegistryError(f"printer '{name}' is missing: {', '.join(missing)}")
        entries[name] = PrinterCredentials(
            name=name,
            host=str(body["host"]),
            serial=str(body["serial"]),
            access_code=str(body["access_code"]),
            model=str(body.get("model", "")),
            notes=str(body.get("notes", "")),
        )
    return entries


def resolve(
    name: str | None,
    *,
    host: str | None = None,
    serial: str | None = None,
    access_code: str | None = None,
) -> PrinterCredentials:
    """Combine a registry entry with any explicit overrides.

    Explicit flags always win, so a registry entry can be tried against a different
    address without editing the file.
    """
    base = PrinterCredentials(name=name or "adhoc", host="", serial="", access_code="")
    if name:
        entries = load()
        if name not in entries:
            known = ", ".join(sorted(entries)) or "none"
            raise RegistryError(f"unknown printer '{name}'. Known: {known}")
        base = entries[name]

    resolved = PrinterCredentials(
        name=base.name,
        host=host or base.host,
        serial=serial or base.serial,
        access_code=access_code or base.access_code,
        model=base.model,
        notes=base.notes,
    )
    missing = [field for field in ("host", "serial", "access_code") if not getattr(resolved, field)]
    if missing:
        raise RegistryError(
            f"missing {', '.join(missing)} — pass --printer NAME or the explicit flags"
        )
    return resolved


def describe() -> str:
    """A listing that never prints an access code."""
    entries = load()
    if not entries:
        return f"no printers registered (create {REGISTRY_PATH.name})"

    lines = [f"{len(entries)} printer(s) in {REGISTRY_PATH.name}:"]
    for name, printer in sorted(entries.items()):
        model = f" [{printer.model}]" if printer.model else ""
        lines.append(f"  {name:<14} {printer.host:<16} {printer.serial}{model}")
    return "\n".join(lines)
