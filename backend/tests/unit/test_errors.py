"""Error taxonomy behaviour."""

from __future__ import annotations

import pytest

from printorian.core.errors import (
    ConflictError,
    NotFoundError,
    PrintorianError,
    ValidationError,
)


def test_default_code_comes_from_the_class() -> None:
    assert NotFoundError().code == "error.not_found"
    assert ConflictError().code == "error.conflict"


def test_explicit_code_overrides_the_class_default() -> None:
    assert ValidationError("error.pricing.quantity").code == "error.pricing.quantity"


def test_details_are_captured() -> None:
    error = ValidationError("error.pricing.quantity", value=0, minimum=1)
    assert error.details == {"value": 0, "minimum": 1}


def test_a_detail_may_be_named_code() -> None:
    """Regression: `code` as a detail used to collide with the constructor.

    It raised `TypeError: got multiple values for argument 'code'` — swallowing the
    real error on exactly the path where diagnosis matters most.
    """
    error = ValidationError("error.pricing.material_price", code="pla-black")

    assert error.code == "error.pricing.material_price"
    assert error.details == {"code": "pla-black"}


def test_setting_an_instance_code_does_not_leak_to_other_instances() -> None:
    ValidationError("error.one")
    assert ValidationError().code == "error.validation"


def test_every_error_is_a_printorian_error() -> None:
    for error_type in (ValidationError, NotFoundError, ConflictError):
        assert issubclass(error_type, PrintorianError)


def test_repr_is_useful_in_a_traceback() -> None:
    text = repr(ValidationError("error.x", field="name"))
    assert "error.x" in text
    assert "name" in text


def test_raising_and_catching_by_base_class() -> None:
    with pytest.raises(PrintorianError) as excinfo:
        raise ConflictError("error.identity.email_taken", email="a@b.c")
    assert excinfo.value.details["email"] == "a@b.c"
