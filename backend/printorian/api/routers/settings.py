"""The farm's settings, and the log of who changed them.

Gated on `MANAGE_PRICING` rather than a new permission, because every key this
serves today is a pricing rate and the permission for changing what the farm
charges already exists. When the other fourteen sections arrive — scheduler
weights, SLA, notifications, access — they are not all one permission, and the
gate moves to the section rather than to the router (`docs/DESIGN-KIT.md` §2.1).

Reads are gated too. A rate is commercially sensitive: `margin_percent` is the
farm's markup, and `VIEW_FINANCIALS` is kept deliberately separate from every
production permission for the same reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from printorian.api.deps import CurrentActor, FarmSettings, requires
from printorian.contexts.identity import Permission
from printorian.contexts.settings import SettingChangeView, SettingUpdate, SettingView

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(requires(Permission.MANAGE_PRICING))],
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


@router.get("/history")
async def history(settings: FarmSettings, key: str | None = None) -> list[SettingChangeView]:
    """«Было · Стало», newest first, for one key or for the whole farm."""
    return await settings.history(key=key)


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
