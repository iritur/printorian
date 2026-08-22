"""The farm's settings: values it may change without a redeploy, and the audit.

Public surface. Other contexts and the API import from here and never from the
modules beneath (`tools/check_context_isolation.py` enforces it).

The one thing worth knowing before using it: **`RateSnapshot` still arrives at the
pricing engine as an argument.** `SettingsService.resolve_rates` builds it at the
read edge, precisely where `RateSnapshot()` was constructed from defaults before,
so ADR-0002's purity is untouched and a quote is still reproducible from the
snapshot pinned to its order (ADR-0020).
"""

from printorian.contexts.settings.catalogue import DEFAULTS, KEYS, RATE_PREFIX
from printorian.contexts.settings.models import Setting, SettingChange
from printorian.contexts.settings.schemas import SettingChangeView, SettingUpdate, SettingView
from printorian.contexts.settings.service import HISTORY_LIMIT, SettingsService

__all__ = [
    "DEFAULTS",
    "HISTORY_LIMIT",
    "KEYS",
    "RATE_PREFIX",
    "Setting",
    "SettingChange",
    "SettingChangeView",
    "SettingUpdate",
    "SettingView",
    "SettingsService",
]
