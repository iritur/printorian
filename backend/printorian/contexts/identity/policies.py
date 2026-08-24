"""Roles and the permission matrix.

The matrix is data, not scattered ``if role == ...`` checks, so "who may do what"
is answerable by reading one table — and testable exhaustively.

V1 hid financial data by omitting XAML bindings, which is not a security boundary.
Here permissions are enforced in the API layer (:mod:`printorian.api.deps`) and the
UI merely reflects them.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Who someone is. Ordered loosely by scope, but capability comes from the matrix."""

    CUSTOMER = "customer"
    OPERATOR = "operator"
    ENGINEER = "engineer"
    MANAGER = "manager"
    OWNER = "owner"


class CustomerKind(StrEnum):
    """Whether the person orders as themselves or on behalf of a company.

    Not a role. A role says what someone may *do* on the farm; this says which
    payment methods and which documents apply to them — an individual pays by
    card, a company is invoiced and needs the invoice to carry its own name.
    """

    PERSON = "person"
    COMPANY = "company"


class Permission(StrEnum):
    """What someone may do."""

    # customer-facing
    PLACE_ORDER = "place_order"
    VIEW_OWN_ORDERS = "view_own_orders"

    # shop floor
    VIEW_PRODUCTION = "view_production"
    OPERATE_PRINTER = "operate_printer"
    ADVANCE_POSTPRODUCTION = "advance_postproduction"
    RECORD_QC = "record_qc"
    #: Make up the parcel. Its own permission rather than part of
    #: `ADVANCE_POSTPRODUCTION`, because the packing bench is a post like any
    #: other and a farm that hires somebody only to pack should be able to say
    #: so — the same reason inspection is separate from doing the work.
    PACK_ORDER = "pack_order"

    # engineering
    PREPARE_PLATE = "prepare_plate"
    MANAGE_LIBRARY = "manage_library"
    #: Write the farm's public journal. Sits beside `MANAGE_LIBRARY` rather than in
    #: management: both are the shop window, and the reports are engineering write-
    #: ups of the farm's own working — the people who can publish a model are the
    #: people who can publish the report explaining it.
    MANAGE_JOURNAL = "manage_journal"

    # management
    VIEW_ALL_ORDERS = "view_all_orders"
    MANAGE_ORDER = "manage_order"
    MANAGE_INVENTORY = "manage_inventory"
    MANAGE_FLEET = "manage_fleet"

    # money — deliberately separate from every production permission
    VIEW_FINANCIALS = "view_financials"
    MANAGE_PRICING = "manage_pricing"
    ISSUE_REFUND = "issue_refund"

    # administration
    MANAGE_USERS = "manage_users"
    #: Edit the farm's own settings — pricing rates, scheduler weights, SLA,
    #: notifications, access, integrations, maintenance. One permission for the
    #: whole settings screen today; the router's docstring records why finer
    #: per-section gating is deferred rather than spread across fifteen here.
    MANAGE_SETTINGS = "manage_settings"
    VIEW_AUDIT_LOG = "view_audit_log"


_CUSTOMER: frozenset[Permission] = frozenset(
    {
        Permission.PLACE_ORDER,
        Permission.VIEW_OWN_ORDERS,
    }
)

_OPERATOR: frozenset[Permission] = frozenset(
    {
        Permission.VIEW_PRODUCTION,
        Permission.OPERATE_PRINTER,
        Permission.ADVANCE_POSTPRODUCTION,
        Permission.RECORD_QC,
        Permission.PACK_ORDER,
    }
)

_ENGINEER: frozenset[Permission] = _OPERATOR | {
    Permission.PREPARE_PLATE,
    Permission.MANAGE_LIBRARY,
    Permission.MANAGE_JOURNAL,
}

_MANAGER: frozenset[Permission] = _ENGINEER | {
    Permission.VIEW_ALL_ORDERS,
    Permission.MANAGE_ORDER,
    Permission.MANAGE_INVENTORY,
    Permission.MANAGE_FLEET,
    Permission.VIEW_FINANCIALS,
    Permission.VIEW_AUDIT_LOG,
}

_OWNER: frozenset[Permission] = frozenset(Permission)

PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.CUSTOMER: _CUSTOMER,
    Role.OPERATOR: _OPERATOR,
    Role.ENGINEER: _ENGINEER,
    Role.MANAGER: _MANAGER,
    Role.OWNER: _OWNER,
}

#: Staff roles see the farm; customers only ever see their own orders.
STAFF_ROLES: frozenset[Role] = frozenset({Role.OPERATOR, Role.ENGINEER, Role.MANAGER, Role.OWNER})


def permissions_for(role: Role) -> frozenset[Permission]:
    return PERMISSIONS[role]


def can(role: Role, permission: Permission) -> bool:
    return permission in PERMISSIONS[role]


def is_staff(role: Role) -> bool:
    return role in STAFF_ROLES
