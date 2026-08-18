"""HTTP delivery layer.

Routers are thin: they translate HTTP into a context use case and back. Business
rules live in contexts, never here — but *authorization* is enforced here, on every
route, via :func:`printorian.api.deps.requires`.
"""

from printorian.api.app import API_VERSION, create_app

__all__ = ["API_VERSION", "create_app"]
