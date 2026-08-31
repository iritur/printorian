import { useCallback, useEffect, useState } from 'react'

import type { Locale, MessageKey } from '@printorian/ui'
import { translate, translateError } from '@printorian/ui'

/**
 * «Диагностика» — section 14 of `design/settings.html`, and the only section of
 * the settings screen where nothing is a setting.
 *
 * The farm has had real diagnostic signals for a while and nowhere to show them.
 * `/health/ready` names each dependency separately with a deliberate
 * `ok` / `degraded` / `failed` distinction, and `/health/workers` reports each
 * loop's last beat — recorded at the *end* of a pass, so a wedged loop is
 * distinguishable from a running one — plus the printers the worker last said it
 * was driving. Until this panel existed the only consumer of any of it was a
 * person with `curl`.
 *
 * This is not monitoring and must not be argued as a substitute for it
 * (INFRASTRUCTURE Stage 5): a dashboard somebody has to open is not an alert. It
 * answers "the farm is behaving oddly — what does it think of itself", which was
 * unanswerable from inside the product.
 *
 * Three rules run through the whole file, and each is a way this screen could
 * have lied:
 *
 * **A verdict this build does not recognise is `unknown`, never `ok`.** The
 * mapping is a whitelist rather than a `!== 'failed'`, so a check the backend
 * adds tomorrow with a fourth verdict shows up grey and unnamed instead of
 * green and wrong (root CLAUDE.md §1).
 *
 * **`degraded` says a different word, not only a different colour.** The whole
 * point of that distinction is that one of them means "serving fine, guarantee
 * not holding" — `wal_archiving` degraded is a backup that has stopped while
 * every request still succeeds. Colour is the second channel; the pill's text is
 * the first, because colour alone is no distinction at all for a reader who
 * cannot separate amber from red.
 *
 * **Every denominator is what answered, never a roster — and the numerator over
 * it is withheld when nothing answered.** "14 of 15 checks" is counted from the
 * checks the probe actually returned: `event_relay` is only reported where a
 * relay is configured, so a fixed 15 would have made a deployment without one
 * permanently look one short. The same rule applied to the rows themselves is
 * `tally` below, and it is the harder half — a row whose verdict is `unknown`
 * was not a reading, so it belongs in neither side of the fraction.
 */

/** Where the console's API lives, matching `SessionProvider`'s client. */
const API_BASE = '/api'

/**
 * What a subsystem is doing, as this panel draws it.
 *
 * `unknown` is not a backend verdict — it is what this panel says when it could
 * not measure: the probe did not answer, the roster names a printer whose
 * reading has lapsed, or the value is one this build has never heard of.
 */
type Verdict = 'ok' | 'degraded' | 'failed' | 'unknown'

/**
 * The kit's `.hv-state` tone for each verdict.
 *
 * `paused` and `error` are two different colours in `harvester/system.css`
 * (`--hv-warn` and `--hv-bad`), which is the visual half of keeping `degraded`
 * apart from `failed`. `offline` is the faint grey the fleet screen uses for a
 * machine nobody has heard from, which is exactly what `unknown` means here —
 * and pointedly not `idle`, the green one.
 */
const TONE: Record<Verdict, string> = {
  ok: 'idle',
  degraded: 'paused',
  failed: 'error',
  unknown: 'offline',
}

/** The stat card's `data-tone`, or none at all for a figure nobody measured. */
const STAT_TONE: Record<Verdict, string | null> = {
  ok: 'good',
  degraded: 'warn',
  failed: 'bad',
  unknown: null,
}

/*
  The word each verdict is spelled with, in the three vocabularies the kit uses:
  a dependency is «в норме», a loop «работает», a printer «на связи». Written as
  literal `MessageKey`s rather than built by interpolation so that the compiler
  proves each one exists — a key missing from either catalogue is a type error
  here, which is the guarantee `frontend/CLAUDE.md` asks for and which a
  `as MessageKey` cast throws away.

  `degraded` cannot occur for a loop or a driver today: neither endpoint has a
  middle state. The entries stay because the alternative is a partial map that
  renders an empty pill the day one is added, and an unlabelled pill is the
  failure this whole file is written against.
*/
const VERDICT_WORD: Record<Verdict, MessageKey> = {
  ok: 'settings.diagnostics.verdict.ok',
  degraded: 'settings.diagnostics.verdict.degraded',
  failed: 'settings.diagnostics.verdict.failed',
  unknown: 'settings.diagnostics.verdict.unknown',
}

const BEAT_WORD: Record<Verdict, MessageKey> = {
  ok: 'settings.diagnostics.beat.ok',
  degraded: 'settings.diagnostics.beat.degraded',
  failed: 'settings.diagnostics.beat.failed',
  unknown: 'settings.diagnostics.beat.unknown',
}

const LINK_WORD: Record<Verdict, MessageKey> = {
  ok: 'settings.diagnostics.link.ok',
  degraded: 'settings.diagnostics.link.degraded',
  failed: 'settings.diagnostics.link.failed',
  unknown: 'settings.diagnostics.link.unknown',
}

/** `/health/ready` — one verdict per dependency, and which ones exist varies. */
interface ReadyBody {
  status: string
  checks: Record<string, string>
}

/** `/health/workers` — the loops, and the printers the worker is driving. */
interface WorkersBody {
  status: string
  loops: Record<string, { state: string; last_beat: string | null }>
  drivers: Record<string, { name: string; state: string; code: string | null; since: string | null }>
}

/**
 * One probe's outcome.
 *
 * `probing` and `unreachable` are deliberately different states. A panel that
 * showed "no answer" for the 200 ms before the first response would teach an
 * operator to disbelieve it, which is the one thing a diagnostics screen cannot
 * afford — the same argument `useHealth` makes for its `PROBING` status.
 */
type Reading<T> =
  | { kind: 'probing' }
  | { kind: 'answered'; body: T; at: Date; latencyMs: number }
  | { kind: 'unreachable'; at: Date; latencyMs: number }

/**
 * Ask one health endpoint and keep whatever it said.
 *
 * **`api.get` cannot be used here, and that is the reason this is a bare
 * `fetch`.** Both endpoints answer 503 with a *full* body when something is
 * wrong — readiness whenever a check has failed, workers whenever a loop is not
 * beating — and `ApiClient` throws on any non-2xx, funnelling the body through
 * `readErrorBody`, which keeps only `{code}`-shaped payloads and discards
 * anything else. The panel would therefore have gone blank in precisely the
 * state it exists to explain. A 503 here is the answer, not the failure.
 */
async function probe<T>(path: string, signal: AbortSignal): Promise<Reading<T>> {
  const started = performance.now()
  try {
    const response = await fetch(`${API_BASE}${path}`, { signal })
    const body = (await response.json()) as T
    return { kind: 'answered', body, at: new Date(), latencyMs: elapsed(started) }
  } catch {
    // Nothing answered, or what answered was not JSON — a proxy's HTML 502, say.
    // Either way no verdict was measured, and the renderer says so rather than
    // drawing an empty list, which reads as "nothing is wrong".
    return { kind: 'unreachable', at: new Date(), latencyMs: elapsed(started) }
  }
}

const elapsed = (started: number) => Math.round(performance.now() - started)

/** A backend verdict, or `unknown` for anything this build cannot name. */
function verdictOf(raw: unknown): Verdict {
  return raw === 'ok' || raw === 'degraded' || raw === 'failed' ? raw : 'unknown'
}

/**
 * A loop's beat as a verdict.
 *
 * `stale` is `failed`, not `degraded`: a loop that has not completed a pass
 * within its own window has stopped doing the farm's work, and `/health/workers`
 * answers 503 for it. `unknown` is the store being unreadable — `Heartbeat`
 * reports that rather than inventing an answer, and so does this.
 */
function loopVerdict(state: string): Verdict {
  if (state === 'beating') return 'ok'
  if (state === 'stale') return 'failed'
  return 'unknown'
}

/** A driver's connection as a verdict. `core.driver_health`'s three states. */
function driverVerdict(state: string): Verdict {
  if (state === 'connected') return 'ok'
  if (state === 'unavailable') return 'failed'
  return 'unknown'
}

/**
 * The verdict a group of rows adds up to.
 *
 * `ok` is returned only when every observed row said `ok` and there was at least
 * one — an empty group is `unknown`, because "nothing reported" and "everything
 * is fine" are the two answers this screen must never confuse.
 */
function overall(verdicts: Verdict[]): Verdict {
  if (verdicts.length === 0) return 'unknown'
  if (verdicts.includes('failed')) return 'failed'
  if (verdicts.includes('degraded')) return 'degraded'
  if (verdicts.includes('unknown')) return 'unknown'
  return 'ok'
}

/** What a stat tile is allowed to say about a group of rows. */
interface Tally {
  /** Rows that answered `ok`. Only ever counted among the measured ones. */
  ok: number
  /** Rows that reported *any* verdict this build recognises. The denominator. */
  measured: number
  /** Rows the farm named and did not measure. Neither numerator nor denominator. */
  unmeasured: number
}

/**
 * Split a group of rows into what was measured and what was not.
 *
 * This exists because the roster and the readings are two different lists that
 * happen to arrive in one object, and the tile has to be built from the second.
 * `Heartbeat.report()` iterates the compile-time constant `LOOPS` and returns
 * **all seven** entries with `state="unknown"` whenever the store cannot be read
 * — no Redis client, or an `mget` that raised — so a body full of rows that
 * measured nothing is the ordinary shape of a Redis outage rather than a corner
 * case. The driver roster does the same for a printer whose reading has lapsed.
 *
 * Counting those rows into the denominator makes the farm look worse with every
 * reading it loses. Counting their absence as a zero in the numerator is the
 * flattering half and the worse one: «0 из 7 циклов» asserts that seven loops
 * have stopped, when what the panel knows is that nobody answered — the exact
 * pair root CLAUDE.md §1 forbids, an invented numerator over a roster.
 */
function tally(verdicts: Verdict[]): Tally {
  const measured = verdicts.filter((verdict) => verdict !== 'unknown')
  return {
    ok: measured.filter((verdict) => verdict === 'ok').length,
    measured: measured.length,
    unmeasured: verdicts.length - measured.length,
  }
}

/**
 * A tile's caption: the denominator that was observed, and the shortfall.
 *
 * Three outcomes have to stay apart, because they are three different
 * instructions to whoever is reading. Nothing measured is «НЕ ИЗМЕРЕНО» beside a
 * withheld figure. Everything measured is the bare fraction. In between — some
 * rows answered and some did not — the fraction is true of the rows it counts
 * and says nothing about the rest, so the «· 2 НЕ ИЗМЕРЕНО» half is what stops a
 * farm with five of seven loops unreadable reading as a farm with five loops.
 */
function noteOf(
  t: (key: MessageKey, details?: Record<string, unknown>) => string,
  of: MessageKey,
  counted: Tally,
): string {
  if (counted.measured === 0) return t('settings.diagnostics.stat.unmeasured')
  const base = t(of, { ok: counted.ok, total: counted.measured })
  if (counted.unmeasured === 0) return base
  return `${base} · ${t('settings.diagnostics.stat.also_unmeasured', { unknown: counted.unmeasured })}`
}

/**
 * The entries of something the server called a mapping.
 *
 * A body that is not shaped as expected yields no rows rather than throwing:
 * this panel is the thing that has to keep working when something else has
 * stopped, which is the same argument `core.driver_health._payload` makes.
 */
function entriesOf<T>(value: unknown): [string, T][] {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return []
  return Object.entries(value as Record<string, T>)
}

export function DiagnosticsPanel({ locale }: { locale: Locale }) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  //: A label the catalogue has no entry for falls back to the raw id. A check
  //: nobody has named yet is still a check the farm is reporting, and dropping
  //: it would leave the screen quietly one row short of the truth.
  const named = (key: string, fallback: string) => translate(locale, key as MessageKey) || fallback
  const hint = (key: string) => translate(locale, key as MessageKey) || ''

  const [ready, setReady] = useState<Reading<ReadyBody>>({ kind: 'probing' })
  const [workers, setWorkers] = useState<Reading<WorkersBody>>({ kind: 'probing' })
  const [busy, setBusy] = useState(true)
  //: Bumped by «Прогнать заново». The kit's own foot line promises the checks
  //: are run on request and not cached, so there is no polling behind this
  //: screen — the panel heads carry the time of the reading instead, which is
  //: what stops an old answer being read as a current one.
  const [run, setRun] = useState(0)

  useEffect(() => {
    let alive = true
    const controller = new AbortController()
    void (async () => {
      setBusy(true)
      const [nextReady, nextWorkers] = await Promise.all([
        probe<ReadyBody>('/health/ready', controller.signal),
        probe<WorkersBody>('/health/workers', controller.signal),
      ])
      // An aborted probe resolves as `unreachable`, so the guard is what stops a
      // torn-down effect writing "no answer" over a perfectly good reading.
      if (!alive) return
      setReady(nextReady)
      setWorkers(nextWorkers)
      setBusy(false)
    })()
    return () => {
      alive = false
      controller.abort()
    }
  }, [run])

  const rerun = useCallback(() => setRun((current) => current + 1), [])

  const checks = ready.kind === 'answered' ? entriesOf<string>(ready.body.checks) : []
  const checkVerdicts = checks.map(([, raw]) => verdictOf(raw))

  const loops = workers.kind === 'answered' ? entriesOf<WorkersBody['loops'][string]>(workers.body.loops) : []
  const loopVerdicts = loops.map(([, loop]) => loopVerdict(String(loop?.state ?? '')))

  const drivers =
    workers.kind === 'answered' ? entriesOf<WorkersBody['drivers'][string]>(workers.body.drivers) : []
  const driverVerdicts = drivers.map(([, driver]) => driverVerdict(String(driver?.state ?? '')))

  //: Each tile is built from what was measured, never from the length of the
  //: list that arrived — the roster is not the reading, and an empty list is
  //: only the loudest case of a group that measured nothing.
  const checksTally = tally(checkVerdicts)
  const loopsTally = tally(loopVerdicts)
  const driversTally = tally(driverVerdicts)

  return (
    <>
      {/*
        Three cards, and the kit draws four. «Аптайм» and «События в очереди»
        are dropped rather than filled: nothing in the system measures either,
        and a tile reading `0 events queued` on a farm whose relay is down is an
        invented number with a nice font (CLAUDE.md §1). They come back when
        something measures them.
      */}
      <div className="hv-grid hv-grid--3">
        <StatCard
          label={t('settings.diagnostics.stat.readiness')}
          value={t(VERDICT_WORD[overall(checkVerdicts)])}
          note={noteOf(t, 'settings.diagnostics.stat.checks', checksTally)}
          verdict={overall(checkVerdicts)}
        />
        {/*
          The figure is withheld, not zeroed, when nothing was measured — and
          «nothing» is not the same question as «no rows». A heartbeat store the
          worker cannot read answers with all seven loops present and every one
          of them `unknown`, so a tile keyed on `loops.length` would have read
          «0 из 7 циклов» on the farm's worst morning: an invented numerator over
          a roster, and flattering in neither direction — it says the loops are
          stopped when what happened is that nobody looked.

          `overall` already withholds the tone for the same case, so the tile
          goes untinted; between them the reader can tell all-fine from
          partly-measured from measured-nothing, which are three different
          things to do next.
        */}
        <StatCard
          label={t('settings.diagnostics.stat.loops')}
          value={loopsTally.measured === 0 ? '—' : String(loopsTally.ok)}
          note={noteOf(t, 'settings.diagnostics.stat.loops_of', loopsTally)}
          verdict={overall(loopVerdicts)}
        />
        {/*
          Same shape, and the roster makes it likelier here: `core.driver_health`
          keeps naming a printer for a window after its reading has lapsed, so a
          farm whose worker has gone still lists its printers with `unknown`
          states. «0 из 6 подключено» would be a claim that six printers are
          off the air; the honest answer is that nobody knows about any of them.
        */}
        <StatCard
          label={t('settings.diagnostics.stat.drivers')}
          value={driversTally.measured === 0 ? '—' : String(driversTally.ok)}
          note={noteOf(t, 'settings.diagnostics.stat.drivers_of', driversTally)}
          verdict={overall(driverVerdicts)}
        />
      </div>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('settings.diagnostics.checks')}</span>
          <span className="hv-panel__aside">{takenAt(locale, ready, t)}</span>
        </div>
        <div className="hv-panel__body--none">
          <Absent reading={ready} rows={checks.length} locale={locale} empty="settings.diagnostics.no_checks" />
          {checks.map(([name, raw]) => (
            <HealthRow
              key={name}
              label={named(`settings.diagnostics.check.${name}`, name)}
              hint={hint(`settings.diagnostics.check.${name}.hint`)}
              verdict={verdictOf(raw)}
              verdictLabel={t(VERDICT_WORD[verdictOf(raw)])}
              //: The kit puts a per-check latency here. Nothing measures one —
              //: the round trip below covers the whole probe — and splitting it
              //: across the checks would be arithmetic presented as a reading.
              figure="—"
            />
          ))}
        </div>
        <div className="hv-panel__foot">
          <span>
            {t('settings.diagnostics.foot')}
            {/*: `answered` and not merely `!== 'probing'`. `probe()` fills
                `latencyMs` from its catch block too, so an unreachable probe
                carries a real number — time-to-failure — and this line calls it
                «ОТВЕТ {ms} МС» / "answered in {ms} ms". Printing it there states
                a round trip that never completed, under a body sentence saying
                the subsystem could not be measured at all (CLAUDE.md §1). */}
            {ready.kind === 'answered' && ` · ${t('settings.diagnostics.latency', { ms: ready.latencyMs })}`}
          </span>
          <button className="hv-btn hv-btn--sm" type="button" disabled={busy} onClick={rerun}>
            {t('settings.diagnostics.rerun')}
          </button>
        </div>
      </section>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('settings.diagnostics.loops')}</span>
          <span className="hv-panel__aside">{takenAt(locale, workers, t)}</span>
        </div>
        <div className="hv-panel__body--none">
          <Absent reading={workers} rows={loops.length} locale={locale} empty="settings.diagnostics.no_loops" />
          {loops.map(([name, loop]) => {
            const verdict = loopVerdict(String(loop?.state ?? ''))
            return (
              <HealthRow
                key={name}
                label={named(`settings.diagnostics.loop.${name}`, name)}
                hint=""
                verdict={verdict}
                verdictLabel={t(BEAT_WORD[verdict])}
                // The last beat is the whole signal: a loop that is running but
                // not finishing passes stops updating this while its process
                // stays alive. Absent is an em dash, never «сейчас».
                figure={clockOf(locale, loop?.last_beat ?? null)}
              />
            )
          })}
        </div>
      </section>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('settings.diagnostics.drivers')}</span>
          <span className="hv-panel__aside">{takenAt(locale, workers, t)}</span>
        </div>
        <div className="hv-panel__body--none">
          {/*
            An empty roster gets its own sentence rather than the generic one.
            `core.driver_health` is explicit that empty means *nothing has been
            published* — no Redis, or a worker down longer than the roster's
            window — and it must never be read as a fleet size. A blank panel
            here would say "this farm has no printers", which is the exact claim
            the two-window design was built to avoid making.
          */}
          <Absent
            reading={workers}
            rows={drivers.length}
            locale={locale}
            empty="settings.diagnostics.no_drivers"
          />
          {drivers.map(([printerId, driver]) => {
            const verdict = driverVerdict(String(driver?.state ?? ''))
            return (
              <HealthRow
                key={printerId}
                label={driver?.name || printerId}
                // The code behind an `unavailable`, rendered through the same
                // translator every API error uses (ADR-0012: the backend sends a
                // code, this side owns the prose). An unreachable printer that
                // cannot say why is one somebody has to go and find.
                hint={driver?.code ? translateError(locale, { code: driver.code }) : ''}
                verdict={verdict}
                verdictLabel={t(LINK_WORD[verdict])}
                figure={clockOf(locale, driver?.since ?? null)}
              />
            )
          })}
        </div>
      </section>
    </>
  )
}

/**
 * What to draw when there are no rows.
 *
 * Three outcomes and three sentences, because collapsing them loses the fact
 * that matters. "We have not asked yet", "we asked and nothing answered", and
 * "the answer named nothing" are different states of the farm, and only the
 * middle one is a reason to go and look at the network.
 */
function Absent<T>({
  reading,
  rows,
  locale,
  empty,
}: {
  reading: Reading<T>
  rows: number
  locale: Locale
  empty: string
}) {
  if (rows > 0) return null
  const key =
    reading.kind === 'probing'
      ? 'settings.diagnostics.probing'
      : reading.kind === 'unreachable'
        ? 'settings.diagnostics.no_answer'
        : empty
  return (
    <p className="hv-micro" style={{ padding: 'var(--hv-3)' }} role="status">
      {translate(locale, key as MessageKey)}
    </p>
  )
}

/** One `.hv-health` row: what it is, what it says, and the figure behind it. */
function HealthRow(props: {
  label: string
  hint: string
  verdict: Verdict
  verdictLabel: string
  figure: string
}) {
  return (
    <div className="hv-health">
      <span>
        <span className="hv-set__name">{props.label}</span>
        {props.hint && <span className="hv-set__hint">{props.hint}</span>}
      </span>
      <span className="hv-state" data-state={TONE[props.verdict]}>
        {props.verdictLabel}
      </span>
      <span className="hv-health__ms">{props.figure}</span>
    </div>
  )
}

/** One of the kit's stat cards, toned by the verdict it is summarising. */
function StatCard(props: { label: string; value: string; note: string; verdict: Verdict }) {
  const tone = STAT_TONE[props.verdict]
  return (
    <div className="hv-frame hv-stat" {...(tone ? { 'data-tone': tone } : {})}>
      <span className="hv-label">{props.label}</span>
      <span className="hv-stat__v">{props.value}</span>
      <span className="hv-micro">{props.note}</span>
    </div>
  )
}

/** «ОБНОВЛЕНО HH:MM:SS», or nothing at all before the first answer. */
function takenAt<T>(
  locale: Locale,
  reading: Reading<T>,
  t: (key: MessageKey, details?: Record<string, unknown>) => string,
): string {
  if (reading.kind === 'probing') return ''
  return t('settings.diagnostics.updated', { at: reading.at.toLocaleTimeString(locale) })
}

/**
 * A stored timestamp as a local time, or an em dash.
 *
 * Absent is an em dash and never a zero or a "now" (frontend/CLAUDE.md): a loop
 * that has never beaten and one that beat a second ago are the two facts this
 * column exists to separate.
 */
function clockOf(locale: Locale, iso: string | null): string {
  if (!iso) return '—'
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? '—' : at.toLocaleTimeString(locale)
}
