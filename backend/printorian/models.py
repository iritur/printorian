"""Every module that defines a table.

SQLAlchemy resolves a foreign key by *name* at flush time, against whatever is
registered on `Base.metadata`. A process that imports only the context it needs
therefore works until the first flush that crosses a boundary — an order writing
its `customer_id`, say — and then fails with `NoReferencedTableError` deep inside
the unit of work, nowhere near the import that was missing.

Alembic already needed this list, or autogenerate silently proposes dropping the
tables it cannot see. It lives here rather than in `alembic/env.py` so that the
worker process and any future entrypoint share one list instead of each keeping
a copy that drifts.

Importing this module is the whole of its API::

    import printorian.models  # noqa: F401

It cannot live in `printorian.core`: core sits *below* contexts in the layering
contract, and this imports them.
"""

from __future__ import annotations

from printorian.contexts.account import models as account_models
from printorian.contexts.catalog import catalogue as catalog_catalogue
from printorian.contexts.catalog import models as catalog_models
from printorian.contexts.fleet import models as fleet_models
from printorian.contexts.identity import models as identity_models
from printorian.contexts.inventory import models as inventory_models
from printorian.contexts.journal import models as journal_models
from printorian.contexts.ordering import models as ordering_models
from printorian.contexts.packaging import models as packaging_models
from printorian.contexts.payments import models as payment_models
from printorian.contexts.postproduction import models as postproduction_models
from printorian.contexts.production import models as production_models
from printorian.core.db import Base

#: The metadata every table is registered on, once this module has been imported.
metadata = Base.metadata

__all__ = [
    "account_models",
    "catalog_catalogue",
    "catalog_models",
    "fleet_models",
    "identity_models",
    "inventory_models",
    "journal_models",
    "metadata",
    "ordering_models",
    "packaging_models",
    "payment_models",
    "postproduction_models",
    "production_models",
]
