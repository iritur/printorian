# Runbook — first boot

Bringing up a farm that has never run: from migrated schema to an owner who can
sign in. Ten minutes, once per deployment.

This is the only point in the system's life where an account is created without an
authenticated person behind it. Everywhere else, staff are created by an owner and
customers by registering — which is the right rule, and leaves exactly this gap for
the deployment that has nobody yet.

---

## Before you start

- The stack is up and `alembic upgrade head` has run (`docs/INFRASTRUCTURE.md`).
- An address for the owner **that can receive mail**. Every password reset goes
  through it, and it is the account that can change every rate the farm charges.
- A password from a generator, in the farm's password manager before you type it
  here. You will not be shown it again — it is Argon2-hashed on the way in and
  cannot be read back out of the database by anyone, including you.

---

## 1. Check the database is empty of people

```bash
docker compose -f deploy/compose.prod.yml exec postgres \
    psql -U printorian -c "SELECT email, role FROM users;"
```

Expect zero rows. **If there are rows, stop and read section 4** — a fresh farm has
no accounts, so anything here means this database came from somewhere else, and one
of the somewhere-elses ships with a published owner password.

## 2. Create the owner

```bash
docker compose -f deploy/compose.prod.yml exec api \
    python tools/provision_owner.py --email owner@yourfarm.ru
```

It prompts for the password twice and prints the address it created.

The password is **read from the terminal and never taken as an argument**. A
password on a command line is in the shell history, in `ps` output, and in the
container's process list for as long as the command runs.

The script refuses if an owner already exists. Provisioning is a first-boot step,
not a password reset, and the two want opposite behaviour: a reset should be easy
to repeat, and this should be impossible to repeat by accident.

## 3. Sign in, and prove the loop closes

Open the console and sign in as the owner. Then, in this order:

1. **Set the farm's real rates.** Until somebody changes one, every rate is the
   default compiled into `contexts/settings/catalogue.py` — sensible numbers, but
   not this farm's. Quotes issued before you fix them are honest quotes at the
   wrong price, and ADR-0020 means each order keeps the rate it was quoted at, so
   they do not silently correct themselves later.

   **There is no settings screen yet** (`HANDOFF.md` §3). Today this is the API,
   with the owner's session token:

   ```bash
   curl -s localhost:8000/settings -H "Authorization: Bearer $TOKEN"
   curl -s -X PUT localhost:8000/settings/pricing.labor_rate_per_hour \
        -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        -d '{"value": 750}'
   ```

   `GET /settings` lists all seventeen keys with current and default values, so it
   is also the inventory of what there is to set — `pricing.labor_rate_per_hour`
   (600), `pricing.electricity_rate_per_kwh` (6.50),
   `pricing.depreciation_per_printer_hour` and the rest. `DELETE /settings/{key}`
   puts one back to its default.

   Changes are audited with who and when. The endpoints require `MANAGE_PRICING`,
   which only the owner has — deliberately separate from every production
   permission, so operating the farm never implies repricing it.
2. **Create the operator accounts** the shop floor will use. One account per person;
   a shared operator login makes every audit record read "the operator".
3. **Register a printer** and watch the fleet board pick it up. `RUNBOOK-FIRST-PRINT`
   takes it from there.

If sign-in fails, it is almost always the environment rather than the account —
check that the API and the `psql` above are pointed at the same database.

---

## 4. The database was not empty

Two cases, and they are not equally bad.

**Accounts in `@example.com`, `@example.org`, `@example.net` or `.example`.**
This database is a developer or test dump. Those domains are reserved by RFC 2606
and RFC 6761 precisely so documentation can use addresses that resolve for nobody,
and `docs/DEVELOPMENT.md` publishes two of them **with their passwords**, one an
owner. Anyone with a copy of this repository has them.

The API refuses to start in production while such an account exists
(`contexts/identity/reserved.py`) — so you will meet this as a container that will
not come up, with the offending addresses in the message. Clear them:

```bash
docker compose -f deploy/compose.prod.yml exec postgres psql -U printorian -c \
  "DELETE FROM users WHERE email LIKE '%@example.com' OR email LIKE '%.example';"
```

Then start again from step 1.

**Know what that DELETE does before running it.** Nothing referencing `users`
restricts the delete, so it always succeeds — the question is only what it leaves.
Sessions, addresses and notification preferences are `CASCADE` and go with the
account. Everything else — orders, journal entries, service operations, settings
audit rows — is `SET NULL`, so those rows survive with the actor blanked out.

On a farm being set up that is exactly right: the developer's rows were never real
work and an orphaned test order is harmless. On a database that turns out to hold
*real* orders it is not a cleanup, it is quietly erasing who did what. If the dump
has anything you would miss, do not clean it — get an empty database and start over.

**Real-looking accounts you did not create.** Do not delete anything. This is a
restore, not a first boot, and it already has an owner — use their credentials, or
the recovery path below.

## 5. Recovery — the owner is locked out

There is no self-service path, deliberately. A script that mints a second owner on
request is a back door with good manners.

Recovery is a hands-on-the-server act, by somebody with shell access to the farm.
`IdentityService.change_password` is no use here — it demands the current password,
which is the thing that has been lost — so recovery writes the hash directly:

```bash
docker compose -f deploy/compose.prod.yml exec api python - <<'EOF'
import asyncio, getpass, sys
from sqlalchemy import select
from printorian.contexts.identity.models import User
from printorian.contexts.identity.service import _hasher
from printorian.core.config import get_settings
from printorian.core.db import Database

EMAIL = "owner@yourfarm.ru"   # <-- the locked-out account

async def main():
    database = Database(get_settings())
    try:
        async for session in database.session():
            user = await session.scalar(select(User).where(User.email == EMAIL))
            if user is None:
                sys.exit(f"no such account: {EMAIL}")
            password = getpass.getpass(f"New password for {EMAIL}: ")
            if len(password) < 10:
                sys.exit("refused: at least 10 characters")
            user.password_hash = _hasher.hash(password)
            await session.commit()
            print(f"password reset for {EMAIL} ({user.role.value})")
    finally:
        await database.dispose()

asyncio.run(main())
EOF
```

It re-hashes with the same Argon2 parameters the application uses, so the account
is indistinguishable from one whose password was changed normally.

**`getpass` needs a real terminal.** It reads the TTY, not stdin, so it cannot be
piped and it hangs forever without one — do not put this or `provision_owner.py`
in a script, a systemd unit, or a `docker compose exec -T`. That hang looks exactly
like a slow database.

Write down who did it and when, in whatever the farm uses for change records. The
point of making this awkward is that it leaves a trace; skipping the trace throws
away the only thing the awkwardness bought.

---

## What this does not cover

Multi-owner farms. Nothing prevents a second owner being created *through the
console* by the first, and nothing here has an opinion about whether that is wise —
it is a policy question for the farm, not a technical one.

The reserved-domain guard checks addresses, not passwords. A dump whose accounts
were renamed to real-looking domains passes it, and there is no way to tell from
the outside that its passwords are known. Restoring a dump you did not make into
production is the thing to not do; no check replaces that.
