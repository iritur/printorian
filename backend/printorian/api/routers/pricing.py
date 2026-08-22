"""Quoting: price an uploaded model, and preview what an option would change.

This is the Phase 1 exit criterion and the scenario's steps 3 and 4 — a transparent
itemized price, and an honest answer to "what happens if I change this?".

The router is thin by design: it turns HTTP into a mesh analysis, an estimate and a
call to the pure pricing engine, then back into JSON. No arithmetic happens here,
and the assembly of the spec lives in `_pricing_spec.py`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from printorian.api.deps import (
    AppSettings,
    Cpu,
    DbSession,
    FarmSettings,
    Models,
    OptionalActor,
    rate_limited,
)
from printorian.api.routers._loyalty import tier_for
from printorian.api.routers._pricing_render import _render, _render_delta
from printorian.api.routers._pricing_spec import (
    FINISH_CATALOGUE,
    _build_spec,
    _material_price,
)
from printorian.contexts.pricing import diff, price
from printorian.core.errors import ValidationError

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.post(
    "/quote",
    dependencies=[Depends(rate_limited("quote", lambda s: s.quote_rate_per_minute))],
)
async def quote(
    db: DbSession,
    models: Models,
    settings: AppSettings,
    settings_store: FarmSettings,
    actor: OptionalActor,
    cpu: Cpu,
    model: Annotated[UploadFile, File()],
    material_code: Annotated[str, Form()],
    #: Every product on the plate, one per colour. `material_code` alone still
    #: works for a single-colour order and for any client that predates this.
    material_codes: Annotated[list[str] | None, Form()] = None,
    quantity: Annotated[int, Form()] = 1,
    colors: Annotated[list[str] | None, Form()] = None,
    scale: Annotated[Decimal, Form()] = Decimal(1),
    finishes: Annotated[list[str] | None, Form()] = None,
    rush: Annotated[bool, Form()] = False,
    include_shipping: Annotated[bool, Form()] = True,
) -> dict[str, Any]:
    """Price an uploaded STL and return the full itemized structure."""
    spec, context = await _build_spec(
        db,
        cpu=cpu,
        model=model,
        material_code=material_code,
        material_codes=material_codes,
        quantity=quantity,
        colors=colors or [],
        scale=scale,
        finishes=finishes or [],
        rush=rush,
        include_shipping=include_shipping,
        # A quote is the point at which the customer means it, so this is where the
        # geometry is kept. Content-addressed, so quoting the same file twice
        # stores it once.
        keep=models,
        uploaded_by=actor.user_id if actor else None,
        max_bytes=settings.max_upload_bytes,
    )
    # The farm's rates, defaults underneath (`contexts.settings`).
    rates = await settings_store.resolve_rates()
    return {
        "model": context,
        # The signed-in caller's loyalty tier, so the configurator quotes the
        # figure the checkout will charge. Anonymous browsing is priced at the
        # standard book, which can only be the higher of the two.
        "breakdown": _render(price(spec, rates, await tier_for(db, actor))),
        # The whole ladder, not just the rung that applied.
        #
        # The kit's «03 :: Размер и количество» shows the threshold reached *and*
        # the next one up, which is the block's entire point: it exists to tell a
        # customer that five more units would cost less each. That needs the rung
        # above the current one, which no single priced quote contains.
        #
        # Empty by default, and the block is then absent rather than invented — a
        # farm that has not configured volume discounts does not offer any.
        "discount_tiers": [
            {"min_quantity": tier.min_quantity, "percent": str(tier.percent)}
            for tier in rates.discounts.tiers
        ],
    }


@router.post(
    "/preview",
    dependencies=[Depends(rate_limited("quote", lambda s: s.quote_rate_per_minute))],
)
async def preview_option(
    db: DbSession,
    actor: OptionalActor,
    settings_store: FarmSettings,
    cpu: Cpu,
    model: Annotated[UploadFile, File()],
    material_code: Annotated[str, Form()],
    #: Every product on the plate, one per colour. `material_code` alone still
    #: works for a single-colour order and for any client that predates this.
    material_codes: Annotated[list[str] | None, Form()] = None,
    quantity: Annotated[int, Form()] = 1,
    colors: Annotated[list[str] | None, Form()] = None,
    scale: Annotated[Decimal, Form()] = Decimal(1),
    finishes: Annotated[list[str] | None, Form()] = None,
    rush: Annotated[bool, Form()] = False,
    include_shipping: Annotated[bool, Form()] = True,
    # -- the option being considered
    to_quantity: Annotated[int | None, Form()] = None,
    to_material_code: Annotated[str | None, Form()] = None,
    to_finishes: Annotated[list[str] | None, Form()] = None,
    to_rush: Annotated[bool | None, Form()] = None,
    to_colors: Annotated[list[str] | None, Form()] = None,
    to_material_codes: Annotated[list[str] | None, Form()] = None,
) -> dict[str, Any]:
    """Answer "what would this option change?" as a per-line delta.

    Scenario step 4. The same engine prices both configurations, so the preview
    cannot disagree with the quote the customer then accepts.
    """
    spec, context = await _build_spec(
        db,
        cpu=cpu,
        model=model,
        material_code=material_code,
        material_codes=material_codes,
        quantity=quantity,
        colors=colors or [],
        scale=scale,
        finishes=finishes or [],
        rush=rush,
        include_shipping=include_shipping,
    )

    changes: dict[str, Any] = {}
    if to_quantity is not None:
        changes["quantity"] = to_quantity
    if to_rush is not None:
        changes["rush"] = to_rush
    if to_finishes is not None:
        unknown = [code for code in to_finishes if code not in FINISH_CATALOGUE]
        if unknown:
            raise ValidationError("error.pricing.unknown_finish", finishes=unknown)
        changes["finishes"] = tuple(FINISH_CATALOGUE[code] for code in to_finishes)
    if to_material_codes or to_material_code is not None:
        changes["material"] = await _material_price(
            db, to_material_codes or [to_material_code or ""]
        )
    if to_colors is not None:
        # Colours are not decoration in the price: each extra one costs a purge
        # when the machine swaps filament mid-plate, so "what would a second
        # colour cost?" is a question a customer can be answered before they
        # commit to it.
        #
        # The count is not checked here. `PriceSpec` already refuses more than
        # `MAX_COLORS` with `error.pricing.too_many_colors`, and a second check
        # would be the same rule in two places — free to drift, and to answer the
        # same failure with two different detail shapes.
        changes["colors"] = tuple(to_colors) if to_colors else ("default",)
    if not changes:
        raise ValidationError("error.pricing.no_option_change")

    rates = await settings_store.resolve_rates()
    # Both sides of the diff at the same tier, or the delta would carry the
    # customer's loyalty discount as though the option had caused it.
    tier = await tier_for(db, actor)
    delta = diff(price(spec, rates, tier), price(spec.with_changes(**changes), rates, tier))
    return {"model": context, "delta": _render_delta(delta)}
