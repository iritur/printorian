"""The prep queue and the job desk.

Two audiences. An engineer works the prep queue: see what needs slicing, record
the plate. Production staff watch the jobs and the wait list, and a manager
releases anything the variance band held.

Nothing here recomputes a price. What a plate costs is pricing's answer; this
layer carries it to production, which owns what to do about the difference
(ADR-0013).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from printorian.api.deps import (
    AppSettings,
    CurrentActor,
    DbSession,
    Models,
    Ordering,
    Plates,
    Production,
    Storage,
    requires,
)
from printorian.contexts.catalog import PreparedPlateView, RecordPlate, read_plate
from printorian.contexts.identity import Permission
from printorian.contexts.production import (
    AssignmentRecordView,
    EstimateVarianceView,
    JobView,
    WaitListEntryView,
    estimate_variances,
)
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import EntityId

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(requires(Permission.VIEW_PRODUCTION))],
)


@router.get("/prep-queue")
async def prep_queue(production: Production) -> list[JobView]:
    """Jobs waiting for an engineer to slice them (ADR-0006).

    Its depth is the metric that decides whether human-gated slicing is still
    working: if this saturates, that is the trigger to reopen headless slicing.
    """
    return await production.prep_queue()


@router.get("/wait-list")
async def wait_list(production: Production) -> list[WaitListEntryView]:
    """Work nothing can take yet, and why.

    ``predicted_start`` is null whenever the wait needs a person rather than time —
    filament to be mounted, or a plate no machine on the farm can print. Clients
    render that distinction instead of inventing a date.
    """
    entries = await production.wait_list()
    return [WaitListEntryView.model_validate(entry) for entry in entries]


@router.get("/plates/find")
async def find_plate(
    plates: Plates,
    model_hash: str,
    material_code: str,
    printer_profile: str,
    scale: Decimal = Decimal(1),
    layout_hash: str = "",
) -> PreparedPlateView:
    """Has this configuration been sliced before?

    What the console asks before opening a slicer, so an engineer is never sent to
    redo work the farm already has.
    """
    plate = await plates.find(
        model_hash=model_hash,
        scale=scale,
        material_code=material_code,
        printer_profile=printer_profile,
        layout_hash=layout_hash,
    )
    if plate is None:
        raise NotFoundError("error.catalog.plate_not_found", model_hash=model_hash)
    return plate


@router.get(
    "/variances",
    dependencies=[Depends(requires(Permission.VIEW_FINANCIALS))],
)
async def variances(
    db: DbSession,
    ordering: Ordering,
    order_id: EntityId | None = None,
    exceeded_only: bool = False,
) -> list[EstimateVarianceView]:
    """What slicing found, against what the customer was quoted (ADR-0013).

    **Declared above `/{job_id}` on purpose.** FastAPI matches in declaration
    order, so the same route written below it resolves as `job_id="variances"`
    and fails as a 422 that names a UUID nobody asked for. There is no warning;
    the test below is the only thing that would notice.

    **`VIEW_FINANCIALS` on top of the router's `VIEW_PRODUCTION`.** A variance is
    a measurement, and this one carries money — CLAUDE.md §1 keeps the money
    permission separate from every production permission precisely so a response
    about seconds cannot quietly start carrying rubles. An engineer who may
    prepare the plate is not thereby someone who may read what it cost.

    Nothing is blanked for a caller who lacks it: this route is refused whole.
    Returning the row with its money fields nulled would make "not permitted"
    indistinguishable from "not measured", and ADR-0007 needs that distinction to
    keep meaning something.

    An unknown `order_id` is a 404 rather than an empty list, for the same reason
    — an empty grid reads as "this order had no variances", which is a claim, and
    the farm has not made it.
    """
    if order_id is not None:
        # Raises `error.ordering.not_found` rather than answering with nothing.
        await ordering.get(order_id)
    rows = await estimate_variances(db, order_id=order_id, exceeded_only=exceeded_only)
    return [EstimateVarianceView.model_validate(row) for row in rows]


@router.get("/{job_id}")
async def get_job(job_id: EntityId, production: Production) -> JobView:
    return await production.get(job_id)


@router.get("/{job_id}/decisions")
async def decisions(job_id: EntityId, production: Production) -> list[AssignmentRecordView]:
    """Why this job went where it went.

    Every candidate the planner considered, the grounds each was rejected on, and
    the winning score broken into components. "Why did job #4127 go to P1S-03?" is
    answerable from here — the question V1 could not answer because it never asked.
    """
    records = await production.decisions_for(job_id)
    return [AssignmentRecordView.model_validate(record) for record in records]


@router.get(
    "/{job_id}/model",
    dependencies=[Depends(requires(Permission.PREPARE_PLATE))],
    response_class=Response,
)
async def download_model(job_id: EntityId, production: Production, models: Models) -> Response:
    """The geometry to open in a slicer.

    Half of ADR-0006's amended loop: the engineer downloads the model here, slices
    it locally, and posts the plate back to `/plate/file`. Before this existed the
    console had nothing to offer — the upload was analysed for its volume and
    discarded, so the queue named a file nobody could obtain.

    Served under the customer's original filename rather than the digest, because a
    directory of hashes is unusable to a person.
    """
    job = await production.get(job_id)
    if job.model_asset_id is None:
        raise NotFoundError("error.catalog.model_not_found", job_id=str(job_id))

    content, filename = await models.content(job.model_asset_id)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{_safe_filename(filename)}"'},
    )


@router.post(
    "/{job_id}/plate/file",
    dependencies=[Depends(requires(Permission.PREPARE_PLATE))],
)
async def upload_plate_file(
    job_id: EntityId,
    plate: Annotated[UploadFile, File()],
    plates: Plates,
    production: Production,
    storage: Storage,
    settings: AppSettings,
    actor: CurrentActor,
    printer_profile: str = "default",
    # The same bound `RecordPlate.copies` carries, declared here because a query
    # parameter is validated *before* the handler runs and the model is validated
    # after `storage.put`. Without it `?copies=0` raised a bare pydantic
    # `ValidationError` out of the handler — unhandled by `api/errors.py`, so a 500
    # with no machine-readable code (ADR-0012) and an uploaded blob orphaned in the
    # object store with no row referencing it.
    copies: Annotated[int | None, Query(ge=1)] = None,
    quoted_cost: Decimal = Decimal(0),
) -> JobView:
    """Take the sliced plate an engineer produced, and read the truth out of it.

    The other half of the loop, and the step that makes a dispatch real: the bytes
    go to the object store, the numbers come from the file itself rather than from
    a form, and the plate is cached under its configuration key so **every later
    order of the same configuration skips prep entirely**.

    Numbers are parsed, never asked for. `read_plate` returns ``parsed=False``
    rather than a guess when the file does not say, and the caller is then told to
    record them by hand — an invented print time would be repriced against as
    though it were the truth (ADR-0013).

    ``copies`` is the exception, and it is asked for because it is *not* parsed.
    How many copies of the model are on the bed is what decides whether a later
    order of this configuration may attach this plate unattended
    (`PreparedPlate.copies`), and `plate_file.py` says plainly that the sliced
    `<plate>` element it would have to be counted out of has never been seen from
    this farm's slicer. A miscount there would not fail loudly; it would attach the
    wrong plate quietly. Omitted, the plate is recorded with no copy count and
    stays usable by every path where a person can look at the bed — the automatic
    one simply declines it.
    """
    content = await plate.read()
    if len(content) > settings.max_upload_bytes:
        raise ValidationError("error.catalog.upload_too_large", size=len(content))

    numbers = read_plate(content)
    if not numbers.parsed:
        raise ValidationError(
            "error.catalog.plate_not_parsed",
            filename=plate.filename or "",
            hint="record the numbers with POST /jobs/{job_id}/plate",
        )

    job = await production.get(job_id)
    stored = await storage.put(content, suffix="3mf")

    recorded = await plates.record(
        RecordPlate(
            model_hash=job.model_hash or stored.digest,
            model_name=job.plate_filename or plate.filename or "",
            scale=job.scale,
            material_code=job.material_type,
            # The engineer's choice of slicing profile, not a property of the
            # job: the same geometry sliced for a P1S and for an X1C are two
            # plates, and the key has to tell them apart.
            printer_profile=printer_profile,
            copies=copies,
            print_minutes=Decimal(numbers.print_minutes or 0),
            filament_grams=dict(numbers.filament_grams),
            filename=plate.filename or "plate.3mf",
            content_sha256=stored.digest,
            storage_path=stored.path,
            size_bytes=stored.size_bytes,
            model_asset_id=job.model_asset_id,
            sliced_by=actor.user_id,
        )
    )
    return await production.attach_prepared_plate(
        job_id,
        plate_id=recorded.id,
        filename=recorded.filename,
        print_minutes=recorded.print_minutes,
        total_grams=recorded.total_grams,
        quoted_cost=quoted_cost,
        prepared_cost=quoted_cost,
        tolerance=settings.price_variance_tolerance,
    )


def _safe_filename(name: str) -> str:
    """A filename safe to put in a header.

    Quotes and newlines in a ``Content-Disposition`` are a header-injection
    vector, and the name here came from a customer's upload.
    """
    cleaned = "".join(c for c in name if c.isprintable() and c not in '"\\\r\n')
    return cleaned[:200] or "model.stl"


@router.post("/{job_id}/plate", dependencies=[Depends(requires(Permission.PREPARE_PLATE))])
async def record_plate(
    job_id: EntityId,
    data: RecordPlate,
    plates: Plates,
    production: Production,
    settings: AppSettings,
    actor: CurrentActor,
    quoted_cost: Decimal = Decimal(0),
    prepared_cost: Decimal = Decimal(0),
) -> JobView:
    """Record what an engineer sliced, and attach it to the job.

    The plate is cached under its configuration key, so **every later order of the
    same configuration skips this step entirely** — the whole of ADR-0006.

    ``sliced_by`` is taken from the caller rather than the body: provenance that a
    client can set is provenance that can be wrong.

    The tolerance comes from configuration, never a constant here (ADR-0013).
    """
    plate = await plates.record(data.model_copy(update={"sliced_by": actor.user_id}))
    return await production.attach_prepared_plate(
        job_id,
        plate_id=plate.id,
        filename=plate.filename,
        print_minutes=plate.print_minutes,
        total_grams=plate.total_grams,
        quoted_cost=quoted_cost,
        prepared_cost=prepared_cost,
        tolerance=settings.price_variance_tolerance,
    )


@router.post("/{job_id}/release", dependencies=[Depends(requires(Permission.MANAGE_ORDER))])
async def release_hold(job_id: EntityId, production: Production) -> JobView:
    """Let a price-held job through once somebody has settled the difference.

    Deliberately ``MANAGE_ORDER`` rather than a production permission: what is
    being approved is money, not a machine.
    """
    return await production.release_hold(job_id)
