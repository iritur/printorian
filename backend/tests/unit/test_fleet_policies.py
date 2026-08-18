"""Secrets at rest, and the eligibility rules the scheduler will filter on."""

from __future__ import annotations

from decimal import Decimal

import pytest

from printorian.contexts.fleet.policies import (
    Eligibility,
    JobRequirements,
    PrinterCapability,
    amortization_per_hour,
    can_take,
    needs_attention,
)
from printorian.core.errors import ConfigurationError, ValidationError
from printorian.core.secrets import SecretBox, is_encrypted, is_set
from printorian.drivers import PrinterState

KEY = "a-development-secret-key-not-for-production"


# ------------------------------------------------------------- secrets


def test_a_secret_round_trips() -> None:
    box = SecretBox(KEY)
    stored = box.encrypt("03d00058")

    assert stored != "03d00058"
    assert is_encrypted(stored)
    assert box.decrypt(stored) == "03d00058"


def test_the_plaintext_never_appears_in_the_ciphertext() -> None:
    """A database dump must not carry the access code in any readable form."""
    stored = SecretBox(KEY).encrypt("03d00058")
    assert "03d00058" not in stored


def test_encryption_is_not_deterministic() -> None:
    """Two identical codes must not produce identical ciphertext.

    Otherwise anyone reading the table could tell which printers share a code.
    """
    box = SecretBox(KEY)
    assert box.encrypt("same") != box.encrypt("same")


def test_a_different_key_cannot_decrypt() -> None:
    stored = SecretBox(KEY).encrypt("03d00058")

    with pytest.raises(ValidationError) as excinfo:
        SecretBox("a-completely-different-key-value").decrypt(stored)
    assert excinfo.value.code == "error.secrets.undecryptable"


def test_plaintext_left_over_from_an_earlier_schema_is_refused() -> None:
    """Silently decrypting whatever is in the column would hide a bad migration."""
    with pytest.raises(ValidationError) as excinfo:
        SecretBox(KEY).decrypt("03d00058")
    assert excinfo.value.code == "error.secrets.not_encrypted"


def test_a_weak_key_is_refused_at_construction() -> None:
    with pytest.raises(ConfigurationError):
        SecretBox("short")
    with pytest.raises(ConfigurationError):
        SecretBox("")


def test_is_set_answers_without_the_key() -> None:
    """What an API returns instead of the secret: set, or not set."""
    assert is_set(SecretBox(KEY).encrypt("x")) is True
    assert is_set(None) is False
    assert is_set("") is False


def test_empty_secrets_are_refused() -> None:
    with pytest.raises(ValidationError):
        SecretBox(KEY).encrypt("")


# --------------------------------------------------------- eligibility


def a_printer(**overrides: object) -> PrinterCapability:
    base = {
        "printer_id": "p1",
        "state": PrinterState.IDLE,
        "width_mm": Decimal(256),
        "depth_mm": Decimal(256),
        "height_mm": Decimal(256),
        "nozzle_diameter_mm": Decimal("0.4"),
        "supports_multi_material": True,
        "loaded": (("PLA", "black", Decimal(800)),),
    }
    return PrinterCapability(**{**base, **overrides})  # type: ignore[arg-type]


def a_job(**overrides: object) -> JobRequirements:
    base = {
        "width_mm": Decimal(100),
        "depth_mm": Decimal(100),
        "height_mm": Decimal(100),
        "material_type": "PLA",
        "grams_required": Decimal(120),
    }
    return JobRequirements(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_capable_idle_printer_is_eligible() -> None:
    assert can_take(a_printer(), a_job()) == Eligibility.ok()


def test_a_busy_printer_is_rejected() -> None:
    result = can_take(a_printer(state=PrinterState.PRINTING), a_job())
    assert not result.eligible
    assert "reject.busy" in result.reasons


def test_a_finished_printer_is_rejected_until_someone_clears_it() -> None:
    """Real hardware reports FINISH with the part still on the bed."""
    result = can_take(a_printer(state=PrinterState.FINISHED), a_job())
    assert "reject.busy" in result.reasons


def test_a_printer_with_no_storage_is_not_capacity() -> None:
    """It connects, authenticates and reports happily — and cannot take the plate."""
    result = can_take(a_printer(storage_available=False), a_job())
    assert "reject.no_storage" in result.reasons


def test_a_job_too_large_for_the_bed_is_rejected() -> None:
    result = can_take(a_printer(), a_job(height_mm=Decimal(400)))
    assert "reject.build_volume" in result.reasons


def test_the_wrong_material_is_rejected() -> None:
    result = can_take(a_printer(), a_job(material_type="PETG"))
    assert "reject.material_not_loaded" in result.reasons


def test_material_matching_ignores_case() -> None:
    assert can_take(a_printer(), a_job(material_type="pla")).eligible


def test_not_enough_filament_is_rejected() -> None:
    """A machine holding 3 g of the right material cannot print a 120 g job."""
    result = can_take(a_printer(loaded=(("PLA", "black", Decimal(3)),)), a_job())
    assert "reject.insufficient_material" in result.reasons


def test_multicolour_needs_a_multi_material_machine() -> None:
    result = can_take(
        a_printer(supports_multi_material=False),
        a_job(colors=("black", "red")),
    )
    assert "reject.no_multi_material" in result.reasons


def test_a_colour_that_is_not_loaded_is_rejected() -> None:
    result = can_take(a_printer(), a_job(colors=("purple",)))
    assert "reject.colour_not_loaded" in result.reasons


def test_maintenance_takes_a_printer_out_of_the_pool() -> None:
    result = can_take(a_printer(in_maintenance=True), a_job())
    assert "reject.in_maintenance" in result.reasons


def test_every_failing_reason_is_reported_not_just_the_first() -> None:
    """ "Why did nothing get scheduled" needs the whole answer, not one clue."""
    result = can_take(
        a_printer(state=PrinterState.ERROR, in_maintenance=True, storage_available=False),
        a_job(height_mm=Decimal(999), material_type="PETG"),
    )
    assert len(result.reasons) >= 4


# ------------------------------------------------------------- upkeep


@pytest.mark.parametrize(
    ("state", "due", "expected"),
    [
        (PrinterState.ERROR, False, True),
        (PrinterState.OFFLINE, False, True),
        (PrinterState.FINISHED, False, True),  # a part is waiting to be removed
        (PrinterState.IDLE, True, True),
        (PrinterState.IDLE, False, False),
        (PrinterState.PRINTING, False, False),
    ],
)
def test_what_needs_attention(state: PrinterState, due: bool, expected: bool) -> None:
    assert needs_attention(state, maintenance_due=due) is expected


def test_amortization_is_per_printing_hour() -> None:
    """Idle time does not wear a machine out, so it must not be charged to jobs."""
    assert amortization_per_hour(Decimal(200_000), 20_000) == Decimal("10.00")
    assert amortization_per_hour(Decimal(200_000), 0) == Decimal(0)
