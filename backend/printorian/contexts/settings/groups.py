"""The panel grouping the settings screen draws over its sections.

The kit groups a section's fields under headings — «Труд», «Оборудование»,
«Налоги и учёт» — rather than one flat list. This maps each field key to its
heading, so the screen renders the same panels the design kit draws. A key absent
here renders under no heading (the section is one undivided panel).

Kept apart from `sections.py` not because it is a different concern but because
the two together — the field catalogue *and* its display grouping — pass the
400-line gate that `tools/check_file_length.py` enforces, and a grouping is the
natural seam to split on.
"""

from __future__ import annotations

from typing import Final

GROUPS: Final[dict[str, str]] = {
    # 01 — Общие
    "general.farm_name": "general.farm",
    "general.farm_timezone": "general.farm",
    "general.farm_open_hour": "general.farm",
    "general.farm_close_hour": "general.farm",
    "general.unattended_printing": "general.farm",
    "general.default_locale": "general.display",
    "general.units": "general.display",
    # 02 — Ценообразование
    "pricing.labor_rate_per_hour": "pricing.labor",
    "pricing.labor_hours_per_print_hour": "pricing.labor",
    "pricing.labor_hours_per_job": "pricing.labor",
    "pricing.engineering_hours_per_resize": "pricing.labor",
    "pricing.postprocess_rate_per_hour": "pricing.labor",
    "pricing.electricity_rate_per_kwh": "pricing.machine",
    "pricing.printer_power_kw": "pricing.machine",
    "pricing.depreciation_per_printer_hour": "pricing.machine",
    "pricing.material_procurement_flat": "pricing.material",
    "pricing.multicolor_purge_grams_per_extra_color": "pricing.material",
    "pricing.overhead_per_print_hour": "pricing.overhead",
    "pricing.failure_buffer_percent": "pricing.overhead",
    "pricing.rush_surcharge_percent": "pricing.overhead",
    "pricing.margin_percent": "pricing.overhead",
    # 04 — Планировщик
    "scheduling.weight_capability_waste": "scheduling.weights",
    "scheduling.weight_material_headroom": "scheduling.weights",
    "scheduling.weight_amortization": "scheduling.weights",
    "scheduling.weight_load_balance": "scheduling.weights",
    "scheduling.due_soon_hours": "scheduling.normalization",
    "scheduling.load_horizon_minutes": "scheduling.normalization",
    "scheduling.expensive_per_hour": "scheduling.normalization",
    "scheduling.comfortable_headroom": "scheduling.normalization",
    "scheduling.scheduler_tick_seconds": "scheduling.normalization",
    "scheduling.waitlist.no_capable_printer": "scheduling.waitlist",
    "scheduling.waitlist.awaiting_capacity": "scheduling.waitlist",
    "scheduling.waitlist.material_not_loaded": "scheduling.waitlist",
    # 05 — Сроки и SLA
    "sla.promise_buffer_percent": "sla.promise",
    "sla.min_lead_hours": "sla.promise",
    "sla.rush_lead_hours": "sla.promise",
    "sla.percent_per_day": "sla.lateness",
    "sla.max_percent": "sla.lateness",
    "sla.sla_sweep_seconds": "sla.lateness",
    "sla.sla_auto_refund": "sla.lateness",
    "sla.price_variance_tolerance": "sla.review",
    "sla.price_review_role": "sla.review",
    # 06 — Склад и материалы
    "inventory.low_stock_grams": "inventory.thresholds",
    "inventory.critical_stock_grams": "inventory.thresholds",
    "inventory.auto_reorder": "inventory.thresholds",
    "inventory.default_lead_days": "inventory.thresholds",
    "inventory.require_drying": "inventory.storage",
    "inventory.drying_valid_hours": "inventory.storage",
    "inventory.writeoff_below_grams": "inventory.storage",
    "inventory.track_lots": "inventory.storage",
    # 07 — Оборудование и сервис
    "service.telemetry_poll_seconds": "service.drivers",
    "service.driver_timeout_seconds": "service.drivers",
    "service.driver_send_retries": "service.drivers",
    "service.pause_on_hms_error": "service.drivers",
    "service.allow_mock_driver": "service.drivers",
    # 08 — Постобработка
    "postprocess.require_quality_check": "postprocess.quality",
    "postprocess.photo_before_packing": "postprocess.quality",
    # 09 — Логистика
    "pricing.packaging_per_unit": "logistics.packaging",
    "pricing.shipping_flat": "logistics.packaging",
    "logistics.volumetric_divisor": "logistics.packaging",
    "logistics.free_shipping_threshold": "logistics.packaging",
    # 10 — Финансы
    "finance.tax_regime": "finance.tax",
    "finance.vat_percent": "finance.tax",
    "finance.prices_include_tax": "finance.tax",
    "finance.rounding_step": "finance.tax",
    "finance.payment_provider": "finance.payments",
    "finance.yookassa_shop_id": "finance.payments",
    "finance.yookassa_secret_key": "finance.payments",
    "finance.prepayment_percent": "finance.payments",
    "finance.invoice_payment": "finance.payments",
    "finance.invoice_due_days": "finance.payments",
    "finance.refund_before_print_percent": "finance.refunds",
    "finance.refund_after_print_percent": "finance.refunds",
    "finance.refund_approval_threshold": "finance.refunds",
    # 11 — Уведомления
    "notify.mail_from": "notify.channels",
    "notify.smtp_host": "notify.channels",
    "notify.telegram_chat_id": "notify.channels",
    "notify.quiet_hours_from": "notify.channels",
    "notify.quiet_hours_to": "notify.channels",
    # 12 — Доступ и безопасность
    "security.session_ttl_hours": "security.sessions",
    "security.password_min_length": "security.sessions",
    "security.password_hasher": "security.sessions",
    "security.require_2fa_for_management": "security.sessions",
    "security.lockout_attempts": "security.sessions",
    "security.audit_retention_days": "security.audit",
    # 13 — Интеграции
    "integrations.slicer_engine": "integrations.slicer",
    "integrations.slicer_path": "integrations.slicer",
    "integrations.slicer_profile": "integrations.slicer",
    "integrations.slicer_timeout_seconds": "integrations.slicer",
    "integrations.bambu_connection": "integrations.printers",
    "integrations.bambu_cloud_account": "integrations.printers",
    "integrations.bambu_transport": "integrations.printers",
    # 15 — Обслуживание системы
    "maintenance.backup_enabled": "maintenance.backup",
    "maintenance.backup_hour": "maintenance.backup",
    "maintenance.backup_retention": "maintenance.backup",
    "maintenance.backup_path": "maintenance.backup",
    "maintenance.model_retention_days": "maintenance.storage",
    "maintenance.telemetry_retention_days": "maintenance.storage",
    "maintenance.maintenance_mode": "maintenance.storage",
}


__all__ = ["GROUPS"]
