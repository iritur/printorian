"""The permission matrix, asserted exhaustively.

V1 hid financial data by not binding it in XAML. Here the rule is data, and these
tests are what stop it drifting.
"""

from __future__ import annotations

import pytest

from printorian.contexts.identity import PERMISSIONS, Permission, Role, can, is_staff


def test_every_role_has_an_entry() -> None:
    assert set(PERMISSIONS) == set(Role)


@pytest.mark.parametrize("role", [Role.CUSTOMER, Role.OPERATOR, Role.ENGINEER])
def test_non_managers_never_see_financials(role: Role) -> None:
    assert not can(role, Permission.VIEW_FINANCIALS)
    assert not can(role, Permission.MANAGE_PRICING)
    assert not can(role, Permission.ISSUE_REFUND)


def test_operator_can_run_the_floor_but_not_the_business() -> None:
    assert can(Role.OPERATOR, Permission.OPERATE_PRINTER)
    assert can(Role.OPERATOR, Permission.ADVANCE_POSTPRODUCTION)
    assert not can(Role.OPERATOR, Permission.MANAGE_ORDER)
    assert not can(Role.OPERATOR, Permission.MANAGE_USERS)


def test_customer_cannot_reach_production() -> None:
    assert can(Role.CUSTOMER, Permission.PLACE_ORDER)
    assert not can(Role.CUSTOMER, Permission.VIEW_PRODUCTION)
    assert not can(Role.CUSTOMER, Permission.VIEW_ALL_ORDERS)


def test_roles_widen_monotonically_through_the_staff_ladder() -> None:
    assert PERMISSIONS[Role.OPERATOR] < PERMISSIONS[Role.ENGINEER]
    assert PERMISSIONS[Role.ENGINEER] < PERMISSIONS[Role.MANAGER]
    assert PERMISSIONS[Role.MANAGER] < PERMISSIONS[Role.OWNER]


def test_owner_holds_every_permission() -> None:
    assert PERMISSIONS[Role.OWNER] == frozenset(Permission)


def test_only_owner_administers_users() -> None:
    holders = [role for role in Role if can(role, Permission.MANAGE_USERS)]
    assert holders == [Role.OWNER]


def test_staff_classification() -> None:
    assert not is_staff(Role.CUSTOMER)
    assert all(is_staff(r) for r in (Role.OPERATOR, Role.ENGINEER, Role.MANAGER, Role.OWNER))
