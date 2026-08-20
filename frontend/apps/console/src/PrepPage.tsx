import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale } from '@printorian/ui'
import { api, translate, translateError, useChrome } from '@printorian/ui'

/** One job waiting for an engineer, as `/jobs/prep-queue` returns it. */
interface PrepJob {
  id: string
  order_id: string
  status: string
  model_asset_id: string | null
  material_type: string
  colors: string[]
  scale: string
  grams_required: string
  estimated_minutes: string
  plate_filename: string | null
}

/**
 * The prep queue — ADR-0006's loop, as two buttons.
 *
 * Download the model, slice it in whatever slicer the engineer already uses,
 * upload the plate back. The server reads print time and per-slot grams out of
 * the file and caches the result under its configuration key, so **every later
 * order of the same configuration skips this screen entirely**.
 *
 * The desktop app used to launch the slicer and watch its export folder. A
 * browser can do neither, and one native capability did not justify a desktop
 * app (ADR-0016) — so the round trip is manual, and the cache is what keeps that
 * from scaling with order volume.
 */
export function PrepPage({ locale }: { locale: Locale }) {
  const [jobs, setJobs] = useState<PrepJob[] | null>(null)

  /* How much is waiting on an engineer — the only number this screen is about. */
  useChrome(jobs ? { meta: [{ label: 'PREP.QUEUE', value: String(jobs.length) }] } : null)
  const [error, setError] = useState<string | null>(null)
  // Keyed by job: two engineers on two machines may be working the queue, and a
  // single `busy` flag would grey out the row someone else is holding.
  const [busy, setBusy] = useState<Record<string, string>>({})
  const [done, setDone] = useState<Record<string, boolean>>({})
  const pickers = useRef<Record<string, HTMLInputElement | null>>({})

  const t = useCallback(
    (key: Parameters<typeof translate>[1]) => translate(locale, key),
    [locale],
  )

  const describe = useCallback(
    (exc: unknown) =>
      exc instanceof ApiError
        ? translateError(locale, { code: exc.code, details: exc.details })
        : translate(locale, 'error.internal'),
    [locale],
  )

  const load = useCallback(async () => {
    try {
      setJobs(await api.get<PrepJob[]>('/jobs/prep-queue'))
      setError(null)
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }, [describe])

  useEffect(() => {
    void load()
  }, [load])

  /**
   * Hand the model to the browser's own download.
   *
   * A blob URL rather than pointing an anchor at the endpoint: the request has
   * to carry the session cookie and be able to surface a 403 as a message, and a
   * bare link would navigate away from the queue on failure.
   */
  const downloadModel = async (job: PrepJob) => {
    setBusy((current) => ({ ...current, [job.id]: 'download' }))
    setError(null)
    try {
      const { blob, filename } = await api.download(`/jobs/${job.id}/model`)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      // Revoking immediately can cancel the download in some browsers; a tick
      // later is enough for the click to have been taken up.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (exc: unknown) {
      setError(describe(exc))
    } finally {
      setBusy((current) => ({ ...current, [job.id]: '' }))
    }
  }

  const uploadPlate = async (job: PrepJob, file: File) => {
    setBusy((current) => ({ ...current, [job.id]: 'upload' }))
    setError(null)
    try {
      const form = new FormData()
      form.append('plate', file)
      await api.upload(`/jobs/${job.id}/plate/file`, form)
      setDone((current) => ({ ...current, [job.id]: true }))
      // The job leaves the queue on success, so the list is refetched rather
      // than patched — its status, its plate and its transitions all moved.
      await load()
    } catch (exc: unknown) {
      setError(describe(exc))
    } finally {
      setBusy((current) => ({ ...current, [job.id]: '' }))
    }
  }

  if (error && jobs === null) return <p className="cfg__error">{error}</p>
  if (jobs === null) return <p>{t('common.loading')}</p>

  return (
    <div className="prep">
      <h2>{t('prep.title')}</h2>
      <p className="prep__steps">{t('prep.steps')}</p>

      {error && <p className="cfg__error">{error}</p>}

      {jobs.length === 0 && <p className="prep__empty">{t('prep.empty')}</p>}

      <ul className="prep__list">
        {jobs.map((job) => {
          const working = busy[job.id] ?? ''
          return (
            <li key={job.id} className="prep__job">
              <div className="prep__meta">
                <strong>{job.plate_filename || job.id.slice(0, 8)}</strong>
                <span>
                  {t('prep.material')}: {job.material_type}
                  {job.colors.length > 0 && ` · ${job.colors.join(', ')}`}
                </span>
                <span>
                  {t('prep.estimate')}: {job.estimated_minutes} min · {job.grams_required} g
                </span>
              </div>

              <div className="prep__actions">
                {/*
                  A job whose model was never stored cannot be prepared here at
                  all. Saying so is better than a download button that 404s —
                  the engineer needs to know it is the record that is missing,
                  not their click.
                */}
                {job.model_asset_id === null ? (
                  <span className="prep__unavailable">{t('prep.no_model')}</span>
                ) : (
                  <button
                    className="hv-btn hv-btn--sm"
                    type="button"
                    disabled={working !== ''}
                    onClick={() => void downloadModel(job)}
                  >
                    {t('prep.download')}
                  </button>
                )}

                <input
                  ref={(element) => {
                    pickers.current[job.id] = element
                  }}
                  type="file"
                  accept=".3mf,.gcode"
                  hidden
                  onChange={(event) => {
                    const chosen = event.target.files?.[0]
                    // Clear it, or picking the same file twice after a failed
                    // upload fires no change event and the button looks dead.
                    event.target.value = ''
                    if (chosen) void uploadPlate(job, chosen)
                  }}
                />
                <button
                  className="hv-btn hv-btn--sm hv-btn--primary"
                  type="button"
                  disabled={working !== ''}
                  onClick={() => pickers.current[job.id]?.click()}
                >
                  {working === 'upload' ? t('prep.uploading') : t('prep.upload')}
                </button>

                {done[job.id] && <span className="prep__done">{t('prep.uploaded')}</span>}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
