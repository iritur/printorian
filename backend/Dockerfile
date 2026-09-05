# Printorian backend — one image, three commands.
#
# The API, the workers and the migrations all run from this image, differing only
# by the command they are given (docs/INFRASTRUCTURE.md §4.2). One image is one
# thing to build, scan, sign, cache and roll back, and it makes "the code that
# serves requests" and "the code that swept the queue" the same code by
# construction rather than by deployment discipline.
#
#   api        uvicorn printorian.api.app:create_app --factory --host 0.0.0.0
#   workers    python -m printorian.workers
#   migrate    alembic upgrade head
#
# Base images carry a tag *and* a digest, and the two are edited as one thing.
#
# The digest is the half Docker acts on — `name:tag@sha256:...` resolves by
# digest and never consults the tag — so a rebuild gets the interpreter the
# release was tested against. `python:3.13-slim` on its own is a moving target,
# and an interpreter that moved underneath a release only shows up in production.
#
# The tag is the half people and updaters act on, and it was missing here until
# #24. `python@sha256:...` does not say which stream the digest came from, so a
# reviewer cannot tell 3.13-slim from `latest`, and an updater has no reference
# point to bump against: Renovate reads a tagless digest as `latest` and would
# walk the base onto it. The tag is what stops that, not decoration.
#
# What the tag does *not* assert is that this digest was published under it.
# Docker does not check, no gate in this repository can, and the pair has never
# been resolved against a registry from here. The tag records the stream this pin
# is required to follow — for python that stream is written down twice, at 13168e4
# ("python:3.13-slim pinned by digest") and in docs/INFRASTRUCTURE.md Stage 1 —
# and the first bump replaces the digest with that stream's head, which is where
# the two finally become one measured fact. `tests/unit/test_base_image_pins.py`
# holds the halves together meanwhile: a pin that loses its tag fails, and so do
# the three python stages disagreeing.
#
# Debian rather than Alpine is load-bearing, and so is *which* Debian:
# `postgresql-client-17` in the runtime stage comes from the base's own apt set
# and trixie is where 17 lives. The release that moves `3.13-slim` onto the next
# Debian stable takes that package's availability with it, and ADR-0019 finds out
# at restore time rather than at build time — so that bump is read, not merged.

# ---------------------------------------------------------------- builder

FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder

# Build tools live here and never reach the runtime layer. Most of the
# dependency set ships manylinux wheels (numpy, asyncpg, cryptography,
# argon2-cffi), but a wheel going missing upstream should slow a build down
# rather than break it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# A virtualenv rather than the system interpreter, so the runtime stage copies
# one self-contained directory and inherits nothing else from this one.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Dependencies resolve from pyproject.toml alone, so this layer is cached across
# every commit that does not change the dependency set — which is nearly all of
# them. Copying the source first would invalidate it on every edit.
# `constraints.txt` beside it: the floors in pyproject say what the code needs,
# the constraints say which versions this image is built from. Without it two
# builds of one commit can ship different dependency sets (`constraints.txt`).
COPY pyproject.toml constraints.txt ./
RUN mkdir -p printorian && touch printorian/__init__.py \
 && pip install --no-cache-dir -c constraints.txt .

# Now the real source, installed over the placeholder.
COPY printorian/ ./printorian/
RUN pip install --no-cache-dir --no-deps .

# pip is a build tool, and it vendors its own dependency set — `pip/_vendor`
# carries msgpack and pkg_resources, which is how two HIGH advisories entered
# an image that installs nothing at runtime. Removed once the last install is
# done, which makes the claim in the runtime stage true rather than aspirational.
RUN pip uninstall --yes pip

# ---------------------------------------------------------------- runtime

FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS runtime

# curl for the healthcheck below; nothing else. No compiler, no pip cache, no
# package index — a runtime that cannot install anything is a runtime an attacker
# cannot install anything into either.
# `apt-get upgrade` alongside a digest-pinned base is deliberate rather than a
# contradiction. The digest fixes *which* image was chosen, so the choice stays
# reviewable and repeatable; the upgrade applies the security patches Debian has
# published since that image was built. Without it the image ships whatever was
# current on the day the tag was cut — CVE-2026-53615 in util-linux arrived
# exactly that way, as nine scanner findings from one unpatched source package.
#
# `postgresql-client-17` is an exception to the minimalism above, and it is here
# because of the drill. `scripts/restore_drill.py` runs in *this* image — it needs
# the models and alembic to check that the restored schema still matches the code,
# and the postgres image has neither — but it shells out to `pg_restore`, which
# this image did not carry. `printorian-drill.service` was therefore pointing at a
# binary that was not there.
#
# Pinned to the server's major version on purpose: `pg_restore` refuses a dump
# taken by a newer server, so an unversioned client turns into a silently broken
# drill on the first major upgrade. Debian trixie ships 17 itself, so this costs
# no third-party apt source.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends curl postgresql-client-17 \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/python3.13/site-packages/pip \
           /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
 && useradd --create-home --uid 10001 printorian

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY tools/ ./tools/
# Operating the farm is part of running it: `scripts/restore_drill.py` runs in
# this image, driven by `printorian-drill.service`. Copying tools/ and not
# scripts/ left that timer pointing at a module the image did not contain.
COPY scripts/ ./scripts/

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # The object store must be a volume, never the container's writable layer:
    # a rebuild would otherwise take every uploaded model with it. Named here so
    # a deployment that forgets to mount it fails at boot — core.storage
    # prepare_root() proves the path at startup — rather than at first upload.
    PRINTORIAN_STORAGE_ROOT=/srv/printorian/storage

RUN mkdir -p /srv/printorian/storage && chown -R printorian:printorian /srv/printorian

USER printorian

EXPOSE 8000

# Liveness only. `/health` never touches a dependency, so a database blip
# restarts nothing; readiness is the proxy's business, not the supervisor's.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "printorian.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
