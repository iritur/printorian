"""The catalogue popup's two composed tables.

Neither belongs to a single context, which is why they live beside the router
rather than inside `contexts.catalog`: «Подходящие материалы» joins the
catalogue's editorial judgements to inventory's stock, and «Цена по количеству»
runs the pricing engine five times over geometry the catalogue holds. The API
layer is the one place allowed to reach across contexts, and this is that
composition — kept out of the router so neither file is mostly the other.

Both return empty rather than raising. A popup that fails to open because one of
its tables could not be computed is worse than one missing that table.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import distinct, func, select

from printorian.api.deps import DbSession, FarmSettings, Models
from printorian.contexts.catalog import EstimationProfile, analyse_stl, estimate
from printorian.contexts.catalog.catalogue import CatalogModel
from printorian.contexts.catalog.catalogue_schemas import (
    ModelHistory,
    PriceRung,
    SuitableMaterial,
)
from printorian.contexts.inventory import InventoryService, MaterialStatus
from printorian.contexts.ordering.models import Order
from printorian.contexts.ordering.promise import promised_hours
from printorian.contexts.pricing import (
    MaterialPrice,
    PriceSpec,
    PrintEstimate,
    price,
)
from printorian.contexts.production import JobStatus
from printorian.contexts.production.models import PrintJob
from printorian.core.cpu import CpuGate


async def _suitable_materials(db: DbSession, model: CatalogModel) -> list[SuitableMaterial]:
    """The popup's «Подходящие материалы» table.

    Composed here rather than inside the catalogue, because it needs three
    contexts and the API layer is the one place allowed to touch more than one.
    The catalogue owns the editorial half — how well each material suits the part;
    inventory owns the stock; and the price difference is arithmetic over the two.

    **Matched by family, not by spec.** The catalogue offers a model "in PETG";
    inventory stocks `petg-black` and `petg-clear` separately. The kit's table
    lists families for the same reason a customer thinks in them — so stock is the
    family's total and the price is its dearest colour, which is the same rule
    `pricingSpec` uses in the configurator: quoting the cheapest would mean
    absorbing the difference whenever somebody picks another.

    **Δ price is stated only when it can be.** It is the per-unit difference in
    material cost against the recommended option, priced on the mass the farm
    actually used last time. With no recommendation, or no measured print, there
    is no honest number and the column stays empty — an estimate here would be the
    one fabricated figure on a screen whose whole claim is that its numbers are
    measured.
    """
    offers = sorted(
        model.materials, key=lambda entry: (not entry.is_recommended, entry.material_code)
    )
    if not offers:
        return []

    inventory = InventoryService(db)
    families: dict[str, tuple[str, Decimal | None, Decimal]] = {}
    for offer in offers:
        table = await inventory.table(family=offer.material_code.upper())
        if not table.rows:
            continue
        # Dearest colour in the family, and the family's whole shelf.
        dearest = max(table.rows, key=lambda spec: spec.sell_price_per_gram)
        families[offer.material_code] = (
            offer.material_code.upper(),
            dearest.sell_price_per_gram,
            sum((spec.total_remaining_grams for spec in table.rows), Decimal(0)),
        )

    baseline = next((offer for offer in offers if offer.is_recommended), None)
    base = families.get(baseline.material_code) if baseline else None
    grams = model.last_print_grams

    rows: list[SuitableMaterial] = []
    for offer in offers:
        found = families.get(offer.material_code)
        delta: Decimal | None = None
        if found and base and found[1] is not None and base[1] is not None and grams is not None:
            delta = ((found[1] - base[1]) * grams).quantize(Decimal("1"))
        rows.append(
            SuitableMaterial(
                code=offer.material_code,
                name=found[0] if found else "",
                suitability=offer.suitability,
                note=offer.note,
                is_recommended=offer.is_recommended,
                price_delta=delta,
                # `None`, not zero: a family the shop does not carry at all is a
                # different fact from one it carries and has run out of.
                stock_grams=found[2] if found else None,
            )
        )
    return rows


#: The quantities the ladder prices, as the kit lists them.
LADDER = (1, 5, 10, 25, 50)


async def _price_ladder(
    db: DbSession, models: Models, model: CatalogModel, cpu: CpuGate, settings: FarmSettings
) -> tuple[list[PriceRung], str]:
    """«Цена по количеству» — five real quotes, not one quote extrapolated.

    Each rung goes through the same engine an order does, on the model's own
    stored geometry. Interpolating from a single price would get the shape wrong
    in both directions: per-job costs do not scale, and discount tiers are steps.

    Returns an empty ladder rather than raising when the model cannot be priced —
    a mesh that is not watertight has no defined volume, and a popup that fails to
    open because of it would be worse than one missing a table.
    """
    offered = sorted(
        model.materials, key=lambda entry: (not entry.is_recommended, entry.material_code)
    )
    if not offered:
        return [], ""

    inventory = InventoryService(db)
    table = await inventory.table(family=offered[0].material_code.upper())
    if not table.rows:
        return [], ""
    # Dearest colour in the family, for the same reason `_material_price` picks it:
    # a quote must never land under what the plate actually costs.
    spec_view = max(table.rows, key=lambda spec: spec.sell_price_per_gram)

    try:
        data, _ = await models.content(model.model_asset_id)
        # Off the loop: this runs while a customer opens a catalogue popup, and a
        # large stored model would otherwise stall every other request (`core.cpu`).
        analysis = await cpu.run(analyse_stl, data)
        if not analysis.is_priceable:
            return [], ""
        prediction = estimate(
            analysis, EstimationProfile(density_g_per_cm3=spec_view.density_g_per_cm3)
        )
    except Exception:  # pragma: no cover - unreadable geometry is not a 500 here
        return [], ""

    material = MaterialPrice(
        spec_code=spec_view.code,
        price_per_gram=spec_view.sell_price_per_gram,
        needs_procurement=spec_view.status is MaterialStatus.NONE,
    )
    rates = await settings.resolve_rates()
    promise = await settings.resolve_promise()

    rungs: list[PriceRung] = []
    previous = Decimal(0)
    for quantity in LADDER:
        breakdown = price(
            PriceSpec(
                estimate=PrintEstimate(
                    print_time=prediction.print_time, material_mass=prediction.material_mass
                ),
                material=material,
                quantity=quantity,
                # One colour, no finishing: the ladder is the *base* price, and
                # the aside below says so. Options belong to the configurator.
                colors=("default",),
                finishes=(),
                include_shipping=False,
            ),
            rates,
        )
        tier = rates.discounts.tier_for(quantity)
        percent = tier.percent if tier else Decimal(0)
        rungs.append(
            PriceRung(
                quantity=quantity,
                unit_price=breakdown.unit_price.amount,
                total=breakdown.total.amount,
                lead_hours=promised_hours(
                    policy=promise,
                    print_minutes=prediction.print_time.minutes,
                    quantity=quantity,
                ),
                discount_percent=percent,
                # The first rung at a new tier is the one worth ordering up to.
                is_threshold=percent > previous,
            )
        )
        previous = percent

    # Everything that makes this ladder differ from what the configurator will
    # quote, said out loud. The two are now one click apart — «Настроить и
    # заказать» carries the model straight over — so an unexplained gap between
    # them reads as one of the numbers being wrong.
    #
    # «ВЕРХНЯЯ ГРАНИЦА» is the useful half: the ladder prices the family's
    # dearest colour because no colour has been chosen yet, so the configurator
    # can come in under this figure but never over it.
    basis = (
        f"{offered[0].material_code.upper()} · 1 ЦВЕТ · БЕЗ ОБРАБОТКИ И ДОСТАВКИ · ВЕРХНЯЯ ГРАНИЦА"
    )
    return rungs, basis


#: A print that has stopped, one way or the other. Anything else is still in
#: flight and is evidence of nothing.
_FINISHED = (JobStatus.SUCCEEDED, JobStatus.FAILED)


async def _history(db: DbSession, model: CatalogModel) -> ModelHistory:
    """«Удачных печатей» and «Повторных заказов», counted from real jobs.

    Matched on the mesh digest rather than the catalogue row, because a job knows
    which *geometry* it printed and not which shop-window entry sent it. That is
    the same content address the plate cache keys on, so a customer who uploaded
    this exact file themselves counts too — which is right: the question is how
    reliably this shape prints, not who clicked what.

    Both figures are `None` when there is nothing to measure. A success rate of
    100% over one print is not a track record, so the count travels with the
    percentage and the screen can say which it is.
    """
    asset = model.asset
    if asset is None or not asset.sha256:
        return ModelHistory()

    finished = select(
        func.count().label("total"),
        func.count().filter(PrintJob.status == JobStatus.SUCCEEDED).label("succeeded"),
    ).where(PrintJob.model_hash == asset.sha256, PrintJob.status.in_(_FINISHED))
    row = (await db.execute(finished)).one()
    total, succeeded = int(row.total or 0), int(row.succeeded or 0)

    success: Decimal | None = None
    if total:
        success = (Decimal(succeeded) * 100 / Decimal(total)).quantize(Decimal("0.1"))

    # Orders that printed this geometry, and how many distinct customers placed
    # them. Anonymous orders have no `customer_id` and cannot be repeat business,
    # so they are excluded from both halves rather than counted as new buyers.
    counts = select(
        func.count(distinct(Order.id)).label("orders"),
        func.count(distinct(Order.customer_id)).label("customers"),
    ).where(
        Order.id.in_(select(PrintJob.order_id).where(PrintJob.model_hash == asset.sha256)),
        Order.customer_id.is_not(None),
    )
    tally = (await db.execute(counts)).one()
    orders, customers = int(tally.orders or 0), int(tally.customers or 0)

    repeat: Decimal | None = None
    if orders:
        # Orders beyond the first from each customer. With eight orders from five
        # customers, three were repeats — 37.5%.
        repeat = (Decimal(orders - customers) * 100 / Decimal(orders)).quantize(Decimal("0.1"))

    return ModelHistory(
        success_rate=success,
        finished_prints=total,
        repeat_share=repeat,
        orders=orders,
    )
