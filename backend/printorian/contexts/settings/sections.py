"""The settings screen's fifteen sections, and every field they show.

Two sources of truth feed this, and neither is re-listed by hand where it is not
already hand-listed:

- `RateSnapshot` (pricing) and `SchedulingPolicy` (scheduler weights) are
  frozen dataclasses whose scalar fields become settings automatically, so a rate
  or weight added later appears on the screen without a second edit — the same
  argument `catalogue.py` has always made about `RateSnapshot`.
- `core.config.Settings` supplies the default for the parameters that already
  run the farm (`scheduler_tick_seconds`, `session_ttl_hours`, …), so "the
  setting's default" and "what the farm did before a row existed" cannot drift.

Everything else — parameters the kit names but no code consumes yet — is declared
here with the kit's own value as its default. They persist and audit correctly;
wiring them into a consumer is the read-edge work in a later stage, and is tracked
in `docs/DESIGN-KIT.md` §2.1.

`currency` is deliberately absent: it is a `RateSnapshot` field, and a rate
snapshot carries its currency *inside itself* (the kit's own note) — so it cannot
be edited like a scalar without deciding what happens to the snapshots already
pinned to orders. That is part of the read-edge stage, not the catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from printorian.contexts.pricing import LOYALTY_LADDER, CustomerTier, RateSnapshot
from printorian.contexts.scheduling import SchedulingPolicy
from printorian.contexts.settings.groups import GROUPS
from printorian.core.config import Settings as CoreSettings


class Kind(StrEnum):
    """How a value is parsed, stored and drawn.

    `TABLE` is for the structured values — the volume ladder and the customer
    tiers — whose editors differ enough from a scalar input that they get their
    own row type rather than a number in a box.
    """

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    ENUM = "enum"
    SECRET = "secret"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One setting the screen draws: its key, where it lives, and what it holds.

    `default` is the code default — the value a reset returns to and an empty
    table prices/schedules with. `options` is non-empty only for enums.
    """

    key: str
    section: str
    kind: Kind
    default: Any
    options: tuple[str, ...] = ()
    #: The panel heading within a section (`pricing.labor`, `general.farm`, …).
    #: `None` means the section is one undivided panel.
    group: str | None = None


@dataclass(frozen=True, slots=True)
class Section:
    """A screen section, in the order the rail lists them."""

    id: str
    fields: tuple[str, ...]


#: A config default, read off the pydantic field rather than by instantiating
#: `CoreSettings` — the latter reads the environment, and the catalogue must
#: answer "what does the code ship" with the environment's voice out of the room.
def _cfg(name: str) -> Any:
    return CoreSettings.model_fields[name].default


def default_tiers() -> tuple[CustomerTier, ...]:
    """The customer tiers as the loyalty ladder defines them, no margin override.

    The `from_spend` thresholds stay in `loyalty.py` — the kit's «Тарифы клиентов»
    table shows the discount and the margin override, not how a tier is *earned*,
    and earning is a loyalty mechanic, not a price-book setting.
    """
    return tuple(
        CustomerTier(code=step.code, discount_percent=step.discount_percent)
        for step in LOYALTY_LADDER
    )


# -- derived from dataclasses --------------------------------------------

#: The three rates the kit shows under a different heading than "pricing".
_RATE_SECTION: Final = {
    "pricing.guard_tier_cliffs": "discounts",
    "pricing.packaging_per_unit": "logistics",
    "pricing.shipping_flat": "logistics",
}


def _rate_specs() -> list[FieldSpec]:
    """Every scalar `RateSnapshot` field, typed and sectioned.

    Structured fields — `discounts` (a ladder) and `currency` — are skipped
    here exactly as `_rate_fields` always skipped them: the first is a table, the
    second is pinned inside the snapshot.
    """
    out: list[FieldSpec] = []
    for field in fields(RateSnapshot):
        default = getattr(RateSnapshot(), field.name)
        if isinstance(default, bool):
            kind = Kind.BOOLEAN
        elif isinstance(default, Decimal):
            kind = Kind.DECIMAL
        elif isinstance(default, int):
            kind = Kind.INTEGER
        else:
            continue
        key = f"pricing.{field.name}"
        out.append(
            FieldSpec(
                key=key, section=_RATE_SECTION.get(key, "pricing"), kind=kind, default=default
            )
        )
    return out


def _scheduling_weight_specs() -> list[FieldSpec]:
    """The scheduler's weights and horizons, from the policy dataclass."""
    return [
        FieldSpec(
            key=f"scheduling.{field.name}",
            section="scheduling",
            kind=Kind.DECIMAL,
            default=getattr(SchedulingPolicy(), field.name),
        )
        for field in fields(SchedulingPolicy)
        if isinstance(getattr(SchedulingPolicy(), field.name), Decimal)
    ]


# -- everything else, declared -------------------------------------------


def _all_specs() -> list[FieldSpec]:
    specs: list[FieldSpec] = []
    specs += _rate_specs()
    specs += _scheduling_weight_specs()

    manual: list[FieldSpec] = [
        # 01 — Общие
        FieldSpec("general.farm_name", "general", Kind.STRING, "KN-SOL.21"),
        FieldSpec(
            "general.farm_timezone",
            "general",
            Kind.ENUM,
            _cfg("farm_timezone"),
            ("Europe/Moscow", "Asia/Yekaterinburg", "Asia/Novosibirsk", "UTC"),
        ),
        FieldSpec("general.farm_open_hour", "general", Kind.INTEGER, _cfg("farm_open_hour")),
        FieldSpec("general.farm_close_hour", "general", Kind.INTEGER, _cfg("farm_close_hour")),
        FieldSpec("general.unattended_printing", "general", Kind.BOOLEAN, True),
        FieldSpec("general.default_locale", "general", Kind.ENUM, "ru", ("ru", "en")),
        FieldSpec("general.units", "general", Kind.ENUM, "metric", ("metric",)),
        # 03 — Скидки и тарифы. The volume ladder is a table, not a number in a
        # box: `resolve_rates` parses the stored JSON back into a `DiscountLadder`,
        # whose own `__post_init__` rejects an inverting ladder.
        FieldSpec("pricing.discounts", "discounts", Kind.TABLE, []),
        # The customer tiers, in the same table shape: code, discount, and the
        # optional margin override. The `from_spend` thresholds that *earn* a tier
        # are not shown here — they are the loyalty ladder, not the price book.
        FieldSpec("pricing.tiers", "discounts", Kind.TABLE, default_tiers()),
        # 04 — Планировщик (config interval + waitlist behaviour)
        FieldSpec(
            "scheduling.scheduler_tick_seconds",
            "scheduling",
            Kind.INTEGER,
            _cfg("scheduler_tick_seconds"),
        ),
        FieldSpec(
            "scheduling.waitlist.no_capable_printer",
            "scheduling",
            Kind.ENUM,
            "notify_engineer",
            ("notify_engineer", "waitlist_only"),
        ),
        FieldSpec(
            "scheduling.waitlist.awaiting_capacity",
            "scheduling",
            Kind.ENUM,
            "show_place_and_forecast",
            ("show_place_and_forecast", "show_place_only"),
        ),
        FieldSpec(
            "scheduling.waitlist.material_not_loaded",
            "scheduling",
            Kind.ENUM,
            "notify_operator",
            ("notify_operator", "waitlist_only"),
        ),
        # 05 — Сроки и SLA
        FieldSpec("sla.promise_buffer_percent", "sla", Kind.DECIMAL, Decimal(40)),
        FieldSpec("sla.min_lead_hours", "sla", Kind.DECIMAL, Decimal(24)),
        FieldSpec("sla.rush_lead_hours", "sla", Kind.DECIMAL, Decimal(18)),
        FieldSpec("sla.percent_per_day", "sla", Kind.DECIMAL, Decimal(5)),
        FieldSpec("sla.max_percent", "sla", Kind.DECIMAL, Decimal(30)),
        FieldSpec("sla.sla_sweep_seconds", "sla", Kind.INTEGER, _cfg("sla_sweep_seconds")),
        FieldSpec("sla.sla_auto_refund", "sla", Kind.BOOLEAN, True),
        # The kit states this as percent (15); the config carries it as a fraction
        # (0.15). The catalogue speaks percent — the read-edge stage converts.
        FieldSpec("sla.price_variance_tolerance", "sla", Kind.DECIMAL, Decimal(15)),
        FieldSpec(
            "sla.price_review_role", "sla", Kind.ENUM, "manager", ("manager", "owner", "engineer")
        ),
        # 06 — Склад и материалы
        FieldSpec("inventory.low_stock_grams", "inventory", Kind.INTEGER, 400),
        FieldSpec("inventory.critical_stock_grams", "inventory", Kind.INTEGER, 150),
        FieldSpec("inventory.auto_reorder", "inventory", Kind.BOOLEAN, True),
        FieldSpec("inventory.default_lead_days", "inventory", Kind.INTEGER, 5),
        FieldSpec("inventory.require_drying", "inventory", Kind.BOOLEAN, True),
        FieldSpec("inventory.drying_valid_hours", "inventory", Kind.INTEGER, 72),
        FieldSpec("inventory.writeoff_below_grams", "inventory", Kind.INTEGER, 30),
        FieldSpec("inventory.track_lots", "inventory", Kind.BOOLEAN, True),
        # 07 — Оборудование и сервис
        FieldSpec(
            "service.telemetry_poll_seconds",
            "service",
            Kind.INTEGER,
            _cfg("telemetry_poll_seconds"),
        ),
        FieldSpec("service.driver_timeout_seconds", "service", Kind.INTEGER, 30),
        FieldSpec("service.driver_send_retries", "service", Kind.INTEGER, 3),
        FieldSpec("service.pause_on_hms_error", "service", Kind.BOOLEAN, True),
        FieldSpec("service.allow_mock_driver", "service", Kind.BOOLEAN, False),
        # 08 — Постобработка
        FieldSpec("postprocess.require_quality_check", "postprocess", Kind.BOOLEAN, True),
        FieldSpec("postprocess.photo_before_packing", "postprocess", Kind.BOOLEAN, False),
        # 09 — Логистика (beyond the two rates above)
        FieldSpec("logistics.volumetric_divisor", "logistics", Kind.INTEGER, 5000),
        FieldSpec("logistics.free_shipping_threshold", "logistics", Kind.INTEGER, 15000),
        # 10 — Финансы
        FieldSpec(
            "finance.tax_regime",
            "finance",
            Kind.ENUM,
            "usn_income_minus_expenses",
            ("usn_income_minus_expenses", "usn_income", "osno", "npd"),
        ),
        FieldSpec("finance.vat_percent", "finance", Kind.INTEGER, 20),
        FieldSpec("finance.prices_include_tax", "finance", Kind.BOOLEAN, True),
        FieldSpec(
            "finance.rounding_step",
            "finance",
            Kind.ENUM,
            "kopeck",
            ("kopeck", "ruble", "ten_rubles"),
        ),
        FieldSpec(
            "finance.payment_provider",
            "finance",
            Kind.ENUM,
            _cfg("payment_provider"),
            ("mock", "yookassa", "tbank"),
        ),
        FieldSpec("finance.yookassa_shop_id", "finance", Kind.STRING, ""),
        FieldSpec("finance.yookassa_secret_key", "finance", Kind.SECRET, ""),
        FieldSpec("finance.prepayment_percent", "finance", Kind.INTEGER, 100),
        FieldSpec("finance.invoice_payment", "finance", Kind.BOOLEAN, True),
        FieldSpec("finance.invoice_due_days", "finance", Kind.INTEGER, 5),
        FieldSpec("finance.refund_before_print_percent", "finance", Kind.INTEGER, 100),
        FieldSpec("finance.refund_after_print_percent", "finance", Kind.INTEGER, 0),
        FieldSpec("finance.refund_approval_threshold", "finance", Kind.INTEGER, 10000),
        # 11 — Уведомления
        FieldSpec("notify.mail_from", "notify", Kind.STRING, "farm@printorian.example"),
        FieldSpec("notify.smtp_host", "notify", Kind.STRING, "smtp.yandex.ru:465"),
        FieldSpec("notify.telegram_chat_id", "notify", Kind.STRING, "-1001884420031"),
        FieldSpec("notify.quiet_hours_from", "notify", Kind.INTEGER, 22),
        FieldSpec("notify.quiet_hours_to", "notify", Kind.INTEGER, 8),
        # 12 — Доступ и безопасность
        FieldSpec(
            "security.session_ttl_hours", "security", Kind.INTEGER, _cfg("session_ttl_hours")
        ),
        FieldSpec("security.password_min_length", "security", Kind.INTEGER, 12),
        FieldSpec("security.password_hasher", "security", Kind.ENUM, "argon2id", ("argon2id",)),
        FieldSpec("security.require_2fa_for_management", "security", Kind.BOOLEAN, False),
        FieldSpec("security.lockout_attempts", "security", Kind.INTEGER, 5),
        FieldSpec("security.audit_retention_days", "security", Kind.INTEGER, 365),
        # 13 — Интеграции
        FieldSpec(
            "integrations.slicer_engine",
            "integrations",
            Kind.ENUM,
            "bambu_studio",
            ("bambu_studio", "orca", "prusa"),
        ),
        FieldSpec(
            "integrations.slicer_path",
            "integrations",
            Kind.STRING,
            "C:/Program Files/Bambu Studio/bambu-studio.exe",
        ),
        FieldSpec(
            "integrations.slicer_profile",
            "integrations",
            Kind.ENUM,
            "0.20_standard",
            ("0.20_standard", "0.16_optimal", "0.28_draft"),
        ),
        FieldSpec("integrations.slicer_timeout_seconds", "integrations", Kind.INTEGER, 180),
        FieldSpec(
            "integrations.bambu_connection",
            "integrations",
            Kind.ENUM,
            "lan_only",
            ("lan_only", "lan_then_cloud", "cloud_only"),
        ),
        FieldSpec("integrations.bambu_cloud_account", "integrations", Kind.STRING, ""),
        FieldSpec(
            "integrations.bambu_transport",
            "integrations",
            Kind.ENUM,
            "mqtt_ftps",
            ("mqtt_ftps", "ftps_only"),
        ),
        # 15 — Обслуживание системы
        FieldSpec("maintenance.backup_enabled", "maintenance", Kind.BOOLEAN, True),
        FieldSpec("maintenance.backup_hour", "maintenance", Kind.INTEGER, 3),
        FieldSpec("maintenance.backup_retention", "maintenance", Kind.INTEGER, 30),
        FieldSpec("maintenance.backup_path", "maintenance", Kind.STRING, "D:/printorian/backups"),
        FieldSpec(
            "maintenance.model_retention_days",
            "maintenance",
            Kind.INTEGER,
            _cfg("model_retention_days"),
        ),
        FieldSpec(
            "maintenance.telemetry_retention_days",
            "maintenance",
            Kind.INTEGER,
            _cfg("telemetry_retention_days"),
        ),
        FieldSpec("maintenance.maintenance_mode", "maintenance", Kind.BOOLEAN, False),
    ]
    specs += manual
    return specs


#: key -> spec, in declaration order. Insertion order is what the screen shows, so
#: this is an ordinary dict rather than a sorted one — a sorted catalogue would put
#: `sla.min_lead_hours` before `sla.percent_per_day` and jumble every section.
FIELDS: Final[dict[str, FieldSpec]] = {
    spec.key: (replace(spec, group=GROUPS.get(spec.key)) if spec.key in GROUPS else spec)
    for spec in _all_specs()
}

#: The 15 sections, in rail order. Diagnostics (section 14) is deliberately absent:
#: it is a read-only health page, not settings, and has no fields to edit.
SECTION_ORDER: Final = (
    "general",
    "pricing",
    "discounts",
    "scheduling",
    "sla",
    "inventory",
    "service",
    "postprocess",
    "logistics",
    "finance",
    "notify",
    "security",
    "integrations",
    "maintenance",
)

SECTIONS: Final[tuple[Section, ...]] = tuple(
    Section(
        id=section_id,
        fields=tuple(spec.key for spec in FIELDS.values() if spec.section == section_id),
    )
    for section_id in SECTION_ORDER
)


__all__ = ["FIELDS", "SECTIONS", "SECTION_ORDER", "FieldSpec", "Kind", "Section"]
