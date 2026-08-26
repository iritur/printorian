"""The farm's settings, and the log of who changed them.

Gated on `MANAGE_SETTINGS`, owner-only. The screen is one surface covering fifteen
sections, and they are not one permission in the long run — scheduler weights are
an engineering judgement, payment credentials are a money judgement, access rules
are an administration judgement. `docs/DESIGN-KIT.md` §2.1 records the per-section
gate as the deferred follow-up; for now the screen is the owner's alone, which is
the same answer `MANAGE_PRICING` gave the rates-only version of this router.

Reads are gated too. `margin_percent` is the farm's markup, secrets are secrets,
and `VIEW_FINANCIALS` is kept deliberately separate from every production
permission for the same reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from printorian.api.deps import AppClock, CurrentActor, DbSession, FarmSettings, Identity, requires
from printorian.contexts.fleet import retention
from printorian.contexts.identity import Permission
from printorian.contexts.settings import SectionView, SettingChangeView, SettingUpdate, SettingView

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(requires(Permission.MANAGE_SETTINGS))],
)


@router.get("")
async def listing(settings: FarmSettings) -> list[SettingView]:
    """Every setting the farm may change, with its default beside it.

    Returns the whole catalogue rather than only the overridden rows: the screen
    draws a row per parameter whether or not anybody has touched it, and a client
    that had to merge the code defaults itself would be a second place for them to
    live.
    """
    return await settings.listing()


@router.get("/sections")
async def sections(settings: FarmSettings) -> list[SectionView]:
    """The screen's fourteen headings, each with its fields in rail order.

    Fourteen and not the kit's fifteen: diagnostics is a read-only health page
    with no fields to edit, so it is deliberately absent from `SECTION_ORDER`.

    Distinct from the flat listing so the client does not re-derive the grouping:
    the rail order and the field order within a section are the screen's layout,
    and the server owns them for the same reason it owns the catalogue. That
    includes keeping each panel's fields contiguous — `groups.in_group_order`.
    """
    return await settings.sections()


@router.get("/history")
async def history(
    settings: FarmSettings, identity: Identity, key: str | None = None
) -> list[SettingChangeView]:
    """«Было · Стало», newest first, for one key or for the whole farm.

    The author's name is resolved here, in the delivery layer, rather than in the
    store: a context may not reach into `identity` to look up a name, and the
    screen should not have to fetch every user to read one column.
    """
    rows = await settings.history(key=key)
    names = await identity.display_names(
        [row.changed_by for row in rows if row.changed_by is not None]
    )
    for row in rows:
        row.changed_by_name = names.get(row.changed_by) if row.changed_by is not None else None
    return rows


@router.put("/{key}")
async def update(
    key: str, body: SettingUpdate, settings: FarmSettings, actor: CurrentActor
) -> SettingView:
    """Override one setting.

    The value is parsed against the catalogue and refused if it does not fit —
    422 rather than a coercion, because a rate quietly turned into the wrong number
    reaches every quote the farm gives afterwards.

    **Quotes already given do not move.** Each order pins the rate snapshot it was
    priced with (ADR-0020), so this changes what the *next* quote costs and nothing
    that has already been agreed.
    """
    return await settings.set_value(key, body.value, by=actor.user_id)


@router.delete("/{key}")
async def reset(key: str, settings: FarmSettings, actor: CurrentActor) -> SettingView:
    """Drop the override and go back to the code default.

    Audited like any other edit: "back to the default" answers *why did the price
    change* exactly as much as a new number does.
    """
    return await settings.reset(key, by=actor.user_id)


@router.post("/reset-rates")
async def reset_rates(settings: FarmSettings, actor: CurrentActor) -> dict[str, int]:
    """Drop every pricing override and return to the code defaults.

    The kit's «Сбросить тарифы к значениям по умолчанию». Safe because ADR-0020
    pins each order's snapshot: this changes what the *next* quote costs and
    nothing already sold. Each deleted row is audited under the author.
    """
    return {"reset": await settings.reset_prefix("pricing.", by=actor.user_id)}


@router.post("/drop-telemetry")
async def drop_telemetry(db: DbSession, clock: AppClock, settings: FarmSettings) -> dict[str, int]:
    """Drop telemetry past retention now — the kit's «Удалить телеметрию…».

    Safe by construction: the cutoff is clamped to the hour rollups have actually
    reached, so a farm whose summarising has stalled drops nothing, and one that
    has never summarised an hour drops nothing at all. Not a settings *edit*, so
    it writes no audit row — it is a data operation the owner may trigger early.
    """
    retention_days = await settings.resolve_int("maintenance.telemetry_retention_days")
    dropped = await retention.drop_telemetry_past_retention(
        db, now=clock.now(), retention_days=retention_days
    )
    return {"dropped": len(dropped)}
