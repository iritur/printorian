"""Gateway adapters.

``manual`` and ``mock`` ship today; ``yookassa`` is written against the documented
API and awaits verification with live merchant credentials. It can collect through
a named method — ``tinkoff_bank`` (T-Pay) among them — or the gateway's own menu.
"""

from printorian.contexts.payments.providers.manual import ManualPaymentProvider
from printorian.contexts.payments.providers.mock import MockPaymentProvider
from printorian.contexts.payments.providers.yookassa import YooKassaProvider

__all__ = ["ManualPaymentProvider", "MockPaymentProvider", "YooKassaProvider"]
