"""Quoting: price an uploaded model, and preview what an option would change.

This is the Phase 1 exit criterion and the scenario's steps 3 and 4 — a transparent
itemized price, and an honest answer to "what happens if I change this?".

The router is thin by design: it turns HTTP into a mesh analysis, an estimate and a
call to the pure pricing engine, then back into JSON. No arithmetic happens here.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from printorian.api.deps import (
    AppSettings,
    Cpu,
    DbSession,
    Models,
    OptionalActor,
    rate_limited,
)
from printorian.api.routers._loyalty import tier_for
from printorian.api.routers._pricing_render import _render, _render_delta
from printorian.contexts.catalog import (
    EstimationProfile,
    MeshAnalysis,
    ModelLibrary,
    analyse_stl,
    estimate,
)
from printorian.contexts.inventory import InventoryService, MaterialStatus
from printorian.contexts.ordering import RUSH_LEAD_HOURS, promised_hours
from printorian.contexts.pricing import (
    FinishOption,
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    RateSnapshot,
    diff,
    price,
)
from printorian.core.cpu import CpuGate
from printorian.core.errors import PayloadTooLargeError, ValidationError
from printorian.core.ids import EntityId
from printorian.core.storage import digest_of

router = APIRouter(prefix="/pricing", tags=["pricing"])

#: Finishes offered to customers (scenario option 2e). Phase 2 moves these into a
#: managed catalogue; the shape is already what the engine consumes.
FINISH_CATALOGUE: dict[str, FinishOption] = {
    "raw": FinishOption(code="raw"),
    "sanded": FinishOption(code="sanded", labor_hours=Decimal("0.4")),
    "primed": FinishOption(code="primed", labor_hours=Decimal("0.6"), flat_fee=Decimal(150)),
    "painted": FinishOption(
        code="painted", labor_hours=Decimal("1.5"), flat_fee=Decimal(400), extra_days=2
    ),
}

_MAX_UPLOAD_BYTES = 80 * 1024 * 1024

#: Statuses meaning the farm already holds the filament. Anything else has to be
#: bought in, which the customer pays for (`material.procurement`).
_IN_STOCK = {MaterialStatus.STOCK, MaterialStatus.IN_PRINTER}

#: How many recently analysed models to remember.
#:
#: Small on purpose: this exists to make *one customer's* configurator session
#: fast, not to be a model library. A few concurrent sessions fit comfortably and
#: the oldest is dropped rather than the process growing without bound.
_ANALYSIS_CACHE_SIZE = 32

#: digest -> analysis, oldest first. A plain dict, because Python dicts preserve
#: insertion order and this needs no more than that.
_analysis_cache: OrderedDict[str, MeshAnalysis] = OrderedDict()


def _analyse_cached(data: bytes) -> MeshAnalysis:
    """Analyse a mesh, remembering the answer for identical bytes.

    Every hover in the configurator re-uploads the same model and asks for a
    price, so without this the server re-parses the whole mesh — the dominant
    cost of a preview — to reach a result it already had. Keyed on a digest of
    the content, which is what makes it safe: `analyse_stl` is pure (ADR-0002's
    reasoning applied to geometry), so identical bytes always yield an identical
    analysis, and a *different* model can never collide onto a cached one.
    """
    digest = hashlib.sha256(data).hexdigest()
    cached = _analysis_cache.get(digest)
    if cached is not None:
        # Refresh its position so an actively-used model is not evicted by a
        # burst of one-off uploads.
        _analysis_cache.move_to_end(digest)
        return cached

    analysis = analyse_stl(data)
    _analysis_cache[digest] = analysis
    if len(_analysis_cache) > _ANALYSIS_CACHE_SIZE:
        _analysis_cache.popitem(last=False)
    return analysis


async def _material_price(db: DbSession, codes: list[str]) -> MaterialPrice:
    """The one material a plate is priced from, chosen from everything on it.

    Two separate decisions, and conflating them was a bug:

    * **Price** comes from the dearest product chosen, so a quote never lands
      under what the plate costs.
    * **Procurement** is true when *any* chosen product is off the shelf. Reading
      it off the priced product alone meant a plate of four colours, three of them
      unstocked, was quoted with no procurement charge whenever the dearest
      happened to be the one in stock.
    """
    specs = [await InventoryService(db).get_by_code(code) for code in codes]
    dearest = max(specs, key=lambda spec: spec.sell_price_per_gram)
    return MaterialPrice(
        spec_code=dearest.code,
        price_per_gram=dearest.sell_price_per_gram,
        # Stock is a database fact, decided here so the engine stays pure.
        needs_procurement=any(spec.status not in _IN_STOCK for spec in specs),
    )


async def _build_spec(
    db: DbSession,
    *,
    cpu: CpuGate,
    model: UploadFile,
    material_code: str,
    material_codes: list[str] | None = None,
    quantity: int,
    colors: list[str],
    scale: Decimal,
    finishes: list[str],
    rush: bool,
    include_shipping: bool,
    keep: ModelLibrary | None = None,
    uploaded_by: EntityId | None = None,
    max_bytes: int = _MAX_UPLOAD_BYTES,
) -> tuple[PriceSpec, dict[str, Any]]:
    """Measure an upload and turn it into a pricing input.

    ``keep`` is supplied by the endpoints where the customer is committing to
    something — a real quote — and omitted by the ones that are exploring, so
    hovering over an option does not write a row per hover. The digest is reported
    either way: it costs one hash, and it is what lets a client ask whether this
    configuration has already been sliced before it commits to anything.
    """
    data = await model.read()
    if len(data) > max_bytes:
        # A second ceiling, below the one `BodySizeLimitMiddleware` already refused
        # the body at: this one is the *decoded part's* size and can be tighter
        # than the global upload limit. Reaching it means the multipart framing was
        # smaller than the file inside it, not that nothing checked earlier.
        raise PayloadTooLargeError(
            "error.catalog.upload_too_large", size=len(data), limit=max_bytes
        )

    # Off the event loop. Analysis is seconds of NumPy on a large mesh, and this
    # process is the only one serving the storefront, the console and the health
    # check — see `core.cpu` for the measurements that make that a bug rather than
    # a preference.
    analysis = await cpu.run(_analyse_cached, data)
    if not analysis.is_priceable:
        # An unclosed mesh has no defined volume. Quoting one anyway would be
        # presenting a guess as a fact.
        raise ValidationError(
            "error.catalog.mesh_not_priceable",
            watertight=str(analysis.is_watertight),
            warnings=[warning.code for warning in analysis.warnings],
        )

    material = await _material_price(db, material_codes or [material_code])
    # Density comes from the priced product; within a family the colours share it.
    spec_view = await InventoryService(db).get_by_code(material.spec_code)
    prediction = estimate(
        analysis,
        EstimationProfile(density_g_per_cm3=spec_view.density_g_per_cm3),
        scale=scale,
    )

    unknown = [code for code in finishes if code not in FINISH_CATALOGUE]
    if unknown:
        raise ValidationError("error.pricing.unknown_finish", finishes=unknown)

    price_spec = PriceSpec(
        estimate=PrintEstimate(
            print_time=prediction.print_time, material_mass=prediction.material_mass
        ),
        material=material,
        quantity=quantity,
        colors=tuple(colors) if colors else ("default",),
        scale=scale,
        finishes=tuple(FINISH_CATALOGUE[code] for code in finishes),
        rush=rush,
        include_shipping=include_shipping,
    )

    # Stored before the context is built, so the asset id can go into it. The
    # analysis is handed over rather than re-derived: these are the same bytes,
    # measured moments ago, and parsing a large mesh twice per quote is the cost
    # the in-process cache above exists to avoid.
    asset = (
        await keep.ingest(
            data,
            filename=model.filename or "model.stl",
            uploaded_by=uploaded_by,
            analysis=analysis,
        )
        if keep is not None
        else None
    )

    context = {
        "model_filename": model.filename,
        # The content address. `plate_key` is built on this, so a client holding it
        # can ask "has this been sliced?" without re-uploading anything — and an
        # order carrying it is an order the prep queue can find geometry for.
        "model_sha256": digest_of(data),
        "model_asset_id": str(asset.id) if asset else None,
        "triangle_count": analysis.triangle_count,
        "volume_cm3": str(analysis.volume.cubic_centimetres),
        "bounding_box_mm": {
            "x": str(analysis.bounding_box.x.millimetres),
            "y": str(analysis.bounding_box.y.millimetres),
            "z": str(analysis.bounding_box.z.millimetres),
        },
        "estimate_source": price_spec.estimate.source.value,
        "estimated_minutes": str(prediction.print_time.minutes),
        "estimated_grams": str(prediction.material_mass.grams),
        "mesh_warnings": [warning.code for warning in analysis.warnings],
        # When the farm will stand behind having it ready. Both figures, because
        # the kit's rush option reads «СРОК 18 Ч ВМЕСТО 74 Ч» — the choice only
        # means anything next to what it replaces. Computed here rather than in
        # the client so the buffer policy has one home (see `promise.py`), and
        # kept out of the breakdown because a lead time is not money (ADR-0002).
        "promised_hours": str(
            promised_hours(
                print_minutes=prediction.print_time.minutes,
                quantity=quantity,
                rush=rush,
            )
        ),
        "rush_hours": str(RUSH_LEAD_HOURS),
    }
    return price_spec, context


@router.post(
    "/quote",
    dependencies=[Depends(rate_limited("quote", lambda s: s.quote_rate_per_minute))],
)
async def quote(
    db: DbSession,
    models: Models,
    settings: AppSettings,
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
    rates = RateSnapshot()
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

    rates = RateSnapshot()
    # Both sides of the diff at the same tier, or the delta would carry the
    # customer's loyalty discount as though the option had caused it.
    tier = await tier_for(db, actor)
    delta = diff(price(spec, rates, tier), price(spec.with_changes(**changes), rates, tier))
    return {"model": context, "delta": _render_delta(delta)}
