"""The parameters themselves — every field the settings screen offers.

Two sources feed this, and neither is re-listed by hand where it is not already
hand-listed:

- `RateSnapshot` (pricing) and `SchedulingPolicy` (scheduler weights) are frozen
  dataclasses whose scalar fields become settings automatically, so a rate or
  weight added later appears on the screen without a second edit — the same
  argument `catalogue.py` has always made about `RateSnapshot`.
- `core.config.Settings` supplies the default for the parameters that already run
  the farm (`scheduler_tick_seconds`, `session_ttl_hours`, …), so "the setting's
  default" and "what the farm did before a row existed" cannot drift.

Everything else — parameters the kit names but no code consumes yet — is declared
here with the kit's own value as its default. They persist and audit correctly;
wiring them into a consumer is the read-edge work in a later stage, and is
tracked in `docs/DESIGN-KIT.md` §2.1.

`currency` is deliberately absent: it is a `RateSnapshot` field, and a rate
snapshot carries its currency *inside itself* (the kit's own note) — so it cannot
be edited like a scalar without deciding what happens to the snapshots already
pinned to orders. That is part of the read-edge stage, not the catalogue.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import Final

from printorian.contexts.pricing import RateSnapshot
from printorian.contexts.scheduling import SchedulingPolicy
from printorian.contexts.settings.spec import FieldSpec, Kind, cfg, default_finishes, default_tiers

# -- derived from dataclasses --------------------------------------------

#: The three rates the kit shows under a different heading than "pricing".
_RATE_SECTION: Final = {
    "pricing.guard_tier_cliffs": "discounts",
    "pricing.packaging_per_unit": "logistics",
    "pricing.shipping_flat": "logistics",
}


def rate_specs() -> list[FieldSpec]:
    """Every scalar `RateSnapshot` field, typed and sectioned.

    Structured fields — `discounts` (a ladder), `zones` (the shipping tariff
    table) and `currency` — are skipped here exactly as `_rate_fields` always
    skipped the first: the tables get their own declared entries below, and the
    currency is pinned inside the snapshot.
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


def scheduling_weight_specs() -> list[FieldSpec]:
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


def manual_specs() -> list[FieldSpec]:
    """The fields no dataclass derives, in the order the sections show them."""
    return [
        # 01 — Общие
        FieldSpec("general.farm_name", "general", Kind.STRING, "KN-SOL.21"),
        FieldSpec(
            "general.farm_timezone",
            "general",
            Kind.ENUM,
            cfg("farm_timezone"),
            ("Europe/Moscow", "Asia/Yekaterinburg", "Asia/Novosibirsk", "UTC"),
        ),
        FieldSpec("general.farm_open_hour", "general", Kind.INTEGER, cfg("farm_open_hour")),
        FieldSpec("general.farm_close_hour", "general", Kind.INTEGER, cfg("farm_close_hour")),
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
            cfg("scheduler_tick_seconds"),
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
        FieldSpec("sla.sla_sweep_seconds", "sla", Kind.INTEGER, cfg("sla_sweep_seconds")),
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
            cfg("telemetry_poll_seconds"),
        ),
        FieldSpec("service.driver_timeout_seconds", "service", Kind.INTEGER, 30),
        FieldSpec("service.driver_send_retries", "service", Kind.INTEGER, 3),
        FieldSpec("service.pause_on_hms_error", "service", Kind.BOOLEAN, True),
        FieldSpec("service.allow_mock_driver", "service", Kind.BOOLEAN, False),
        # 08 — Постобработка
        # 08 — Постобработка. The operations catalogue the farm sells, in the same
        # table shape as the tiers: a code the storefront already knows, with the
        # norm-hours and the flat fee editable beside it.
        #
        # `postprocess.*` and deliberately not `pricing.finishes`, for two reasons
        # a reader will otherwise re-litigate. «Сбросить тарифы» is
        # `reset_prefix("pricing.")`, and an owner resetting the rate book has not
        # asked to throw away the norm-hours their finishing station is measured
        # against. And `resolve_rates` builds a `RateSnapshot`, which has no
        # finishes field and must not gain one — `snapshot_id` hashes the field
        # names, so a new field changes the hash of every rebuilt historical
        # snapshot and the cached-plate path then refuses every order already paid.
        FieldSpec("postprocess.operations", "postprocess", Kind.TABLE, default_finishes()),
        FieldSpec("postprocess.require_quality_check", "postprocess", Kind.BOOLEAN, True),
        FieldSpec("postprocess.photo_before_packing", "postprocess", Kind.BOOLEAN, False),
        # 09 — Логистика (beyond the two rates above)
        #
        # The zone tariff, and the third `Kind.TABLE` field after the volume
        # ladder and the customer tiers. Empty by default: a farm that has drawn
        # no zones ships at `pricing.shipping_flat`, exactly as it did before the
        # table existed. `resolve_rates` maps it onto `RateSnapshot.zones`, so
        # editing it moves the *next* quote and nothing already sold (ADR-0020).
        FieldSpec("logistics.zones", "logistics", Kind.TABLE, []),
        # Also still unread: volumetric weight needs a bounding box, and the box
        # that matters is the *parcel's* rather than the part's. It belongs with
        # the shipment, which this slice does not build.
        FieldSpec("logistics.volumetric_divisor", "logistics", Kind.INTEGER, 5000),
        # Deliberately still unread, and that is a judgement rather than a miss.
        # Shipping sits *inside* the base that rush, the volume discount and
        # margin are all taken over (`engine._adjustment_lines` passes every line
        # code as the base), so "free over 15 000 ₽" compared against an order
        # total is circular — the total already contains the shipping and the
        # margin taken on it. It needs a defined base, which is its own decision
        # with its own test. Wiring it carelessly to make the row look consumed
        # would invent a number.
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
            cfg("payment_provider"),
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
        FieldSpec("security.session_ttl_hours", "security", Kind.INTEGER, cfg("session_ttl_hours")),
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
            cfg("model_retention_days"),
        ),
        FieldSpec(
            "maintenance.telemetry_retention_days",
            "maintenance",
            Kind.INTEGER,
            cfg("telemetry_retention_days"),
        ),
        FieldSpec("maintenance.maintenance_mode", "maintenance", Kind.BOOLEAN, False),
    ]


__all__ = ["manual_specs", "rate_specs", "scheduling_weight_specs"]
