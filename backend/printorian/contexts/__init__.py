"""Business contexts.

Each subpackage is a bounded context with a fixed shape (ARCHITECTURE §3)::

    models.py     SQLAlchemy ORM
    schemas.py    Pydantic DTOs
    service.py    use cases
    policies.py   named business rules
    events.py     events this context emits
    __init__.py   the PUBLIC interface — the only thing other contexts may import

A context may import :mod:`printorian.core` and another context's ``__init__``.
Reaching into another context's ``models`` or ``service`` is a contract violation
and fails CI (``tools/check_context_isolation.py``).
"""
