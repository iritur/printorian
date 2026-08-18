"""Gateway adapters.

``manual`` and ``mock`` ship today; ``yookassa`` is written against the documented
API and awaits verification with live merchant credentials.
"""

from printorian.contexts.payments.providers.manual import ManualPaymentProvider
from printorian.contexts.payments.providers.mock import MockPaymentProvider

__all__ = ["ManualPaymentProvider", "MockPaymentProvider"]
