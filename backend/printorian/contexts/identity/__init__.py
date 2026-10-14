"""Identity — users, roles, permissions, sessions.

Public interface. Other contexts and the API import from here and nowhere deeper.
"""

from printorian.contexts.identity.api_keys import KEY_PREFIX, ApiKeyService, is_api_key
from printorian.contexts.identity.policies import (
    KEY_PERMISSIONS,
    PERMISSIONS,
    STAFF_ROLES,
    CustomerKind,
    Permission,
    Role,
    can,
    is_staff,
    permissions_for,
)
from printorian.contexts.identity.reserved import (
    RESERVED_SUFFIXES,
    refusal_message,
    reserved_domain_accounts,
)
from printorian.contexts.identity.schemas import (
    Actor,
    ApiKeyView,
    ChangePassword,
    CreateApiKey,
    CreateUser,
    IssuedApiKey,
    SessionGranted,
    SessionView,
    SignIn,
    UpdateProfile,
    UserView,
)
from printorian.contexts.identity.service import (
    MIN_PASSWORD_LENGTH,
    SEEN_GRANULARITY,
    IdentityService,
    actor_of,
)

__all__ = [
    "KEY_PERMISSIONS",
    "KEY_PREFIX",
    "MIN_PASSWORD_LENGTH",
    "PERMISSIONS",
    "RESERVED_SUFFIXES",
    "SEEN_GRANULARITY",
    "STAFF_ROLES",
    "Actor",
    "ApiKeyService",
    "ApiKeyView",
    "ChangePassword",
    "CreateApiKey",
    "CreateUser",
    "CustomerKind",
    "IdentityService",
    "IssuedApiKey",
    "Permission",
    "Role",
    "SessionGranted",
    "SessionView",
    "SignIn",
    "UpdateProfile",
    "UserView",
    "actor_of",
    "can",
    "is_api_key",
    "is_staff",
    "permissions_for",
    "refusal_message",
    "reserved_domain_accounts",
]
