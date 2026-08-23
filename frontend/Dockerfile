# Printorian frontends — the console image, and the storefront bundle.
#
# Build context is the *repository root*, not frontend/. That is not an
# oversight: the TypeScript API client is generated from the backend's OpenAPI
# schema and never hand-edited (ADR-0005), so a frontend build that cannot see
# the backend is a frontend built against a contract nobody checked.
#
# The schema is exported here rather than taken as a build input, which is what
# makes "backend and frontend from one commit" a property of the build instead of
# a property of running the right commands in the right order. The release unit
# is the commit (docs/INFRASTRUCTURE.md §4.2).
#
#   docker build -f frontend/Dockerfile --target console  -t printorian-console .
#   docker build -f frontend/Dockerfile --target web-dist -t printorian-web .
#
# `web-dist` is a bundle, not a server: the storefront's static files are served
# by Caddy on the edge VPS (ADR-0016), so the image exists only to carry them
# there, versioned and rolled back like everything else.

# ----------------------------------------------------------------- schema
# Exporting OpenAPI needs the backend importable, so this is the backend's
# dependency set — but none of it reaches either final stage.

FROM python@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS schema

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY backend/pyproject.toml ./
RUN mkdir -p printorian && touch printorian/__init__.py \
 && pip install --no-cache-dir .

COPY backend/printorian/ ./printorian/
COPY backend/tools/ ./tools/
RUN pip install --no-cache-dir --no-deps . \
 && python tools/export_openapi.py --out /openapi.json

# ---------------------------------------------------------------- builder

FROM node@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS builder

WORKDIR /build

# Manifests first, so the dependency layer survives every commit that does not
# change them. `npm ci` needs the workspace members' manifests to resolve the
# workspace protocol, so they come along — but not their sources.
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/apps/web/package.json ./apps/web/
COPY frontend/apps/console/package.json ./apps/console/
COPY frontend/packages/ui/package.json ./packages/ui/
COPY frontend/packages/events/package.json ./packages/events/
COPY frontend/packages/api-client/package.json ./packages/api-client/
RUN npm ci

COPY frontend/ ./

# The generator resolves the schema relative to the repo root by default; it is
# given explicitly here because this stage has no backend/ directory beside it.
COPY --from=schema /openapi.json /openapi.json
RUN node scripts/generate-api-client.mjs --schema /openapi.json

# Typecheck before build. `vite build` does not typecheck, so without this the
# image would happily ship a bundle whose types disagree with the contract it was
# just generated from — the exact failure ADR-0005 is meant to make impossible.
RUN npm run typecheck && npm run build

# ---------------------------------------------------------------- console
# Served from the farm server on the LAN (ADR-0016). Same-origin with the API,
# so Caddy also proxies /api — no CORS, no bearer token.

FROM caddy@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS console

COPY --from=builder /build/apps/console/dist /srv
COPY deploy/console.Caddyfile /etc/caddy/Caddyfile

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1

# --------------------------------------------------------------- web-dist
# The storefront bundle alone. `scratch` because this is cargo, not a service:
#   docker create --name x printorian-web && docker cp x:/dist ./storefront

FROM scratch AS web-dist
COPY --from=builder /build/apps/web/dist /dist

# -------------------------------------------------------------- storefront
# The storefront bundle *served*, for a farm that has no edge yet.
#
# `web-dist` above stays the artifact the edge will actually receive — cargo,
# not a service (ADR-0016). This stage is the rehearsal one: it is the same
# bundle behind a Caddy whose `/api` prefix matches both the Vite dev proxy and
# what the edge will use, so ordering and quoting can be exercised on the farm
# before Stage 3 exists.
#
# Kept as a separate target rather than replacing `web-dist`, because the two
# have opposite purposes and merging them would quietly make the on-prem box the
# place the storefront runs — which is the arrangement ADR-0016 exists to avoid.
# `deploy/compose.prod.yml` puts it behind a profile so it never starts by
# default. `deploy/storefront.Caddyfile` lists how it differs from the edge.

FROM caddy@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS storefront

COPY --from=builder /build/apps/web/dist /srv
COPY deploy/storefront.Caddyfile /etc/caddy/Caddyfile

EXPOSE 8081
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8081/ >/dev/null || exit 1
