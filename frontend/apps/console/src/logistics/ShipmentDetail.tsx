import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import { api, translate, translateError } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { formatDays, formatStamp, kindKey, sourceKey, statusKey } from './format'
import { EVENT_KINDS } from './types'
import type { EventKind, Shipment } from './types'

/**
 * One parcel: where it is, the promise it carries, and «История трека».
 *
 * The kit's «Куда» (address from the cabinet) and «Что и почём» (the delivery
 * calculation) are not drawn: the address lives on the order and the money
 * behind `VIEW_FINANCIALS`, and neither is served by `/logistics`. A panel of
 * dashes under «Что и почём» would read as "measured, and free" (ADR-0007).
 *
 * Every write names its full path in one literal — the docs gate that maps
 * routes to their consumers reads the console's string literals.
 */
export function ShipmentDetail({
  shipment,
  locale,
  mayPack,
  onChanged,
  onClose,
}: {
  shipment: Shipment
  locale: Locale
  mayPack: boolean
  onChanged: (shipment: Shipment) => void
  onClose: () => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const [error, setError] = useState<string | null>(null)
  const [kind, setKind] = useState<EventKind>('scan')
  const [note, setNote] = useState('')
  const [tracking, setTracking] = useState(shipment.tracking_number ?? '')

  const post = async (path: string, body: unknown) => {
    try {
      const next = await api.post<Shipment>(path, body)
      setError(null)
      onChanged(next)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }

  const closed = shipment.status === 'delivered' || shipment.status === 'returned'

  return (
    <div className="hv-overlay" role="dialog" aria-modal="true" aria-labelledby="lg-shipment-t">
      <div className="hv-modal">
        <div className="hv-modal__body hv-stack">
          <div className="hv-row hv-row--between">
            <h3 id="lg-shipment-t">{t('lg.detail.title', { order: shipment.order_number })}</h3>
            <button
              className="hv-os__x"
              type="button"
              onClick={onClose}
              aria-label={t('common.close')}
            >
              ✕
            </button>
          </div>

          {error && (
            <p className="hv-hint hv-bad" role="alert">
              {error}
            </p>
          )}

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('lg.detail.where')}</span>
              <span className="hv-panel__aside">{t(statusKey(shipment.status))}</span>
            </div>
            <div className="hv-panel__body">
              <dl className="hv-kv">
                <dt>{t('lg.detail.carrier')}</dt>
                <dd>{shipment.carrier_code || '—'}</dd>
                <dt>{t('lg.detail.zone')}</dt>
                {/* No zone claimed the postcode: a dash, not a zone of nought days. */}
                <dd>{shipment.zone_code ?? '—'}</dd>
                <dt>{t('lg.detail.promised')}</dt>
                <dd>
                  {shipment.promised_days === null
                    ? '—'
                    : formatDays(shipment.promised_days, locale)}
                </dd>
                <dt>{t('lg.detail.shipped')}</dt>
                <dd>{formatStamp(shipment.shipped_at, locale)}</dd>
                <dt>{t('lg.detail.transit')}</dt>
                <dd>
                  {shipment.transit_days === null
                    ? t('lg.detail.days_out', { days: formatDays(shipment.days_out, locale) })
                    : formatDays(shipment.transit_days, locale)}
                  {shipment.on_time === true && ` · ${t('lg.detail.on_time')}`}
                  {shipment.on_time === false && ` · ${t('lg.detail.late')}`}
                </dd>
                <dt>{t('lg.detail.tracking')}</dt>
                <dd>{shipment.tracking_number ?? '—'}</dd>
              </dl>
            </div>
            {mayPack && !closed && (
              <div className="hv-panel__foot">
                <input
                  aria-label={t('lg.detail.tracking')}
                  placeholder={t('lg.detail.tracking')}
                  value={tracking}
                  onChange={(event) => setTracking(event.target.value)}
                />
                <button
                  className="hv-btn hv-btn--sm"
                  type="button"
                  disabled={tracking.trim() === ''}
                  onClick={() =>
                    void post(`/logistics/shipments/${shipment.id}/tracking`, {
                      tracking_number: tracking.trim(),
                    })
                  }
                >
                  {t('lg.action.track')}
                </button>
              </div>
            )}
          </section>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('lg.history.title')}</span>
              <span className="hv-panel__aside">{t('lg.history.aside')}</span>
            </div>
            <div className="hv-panel__body--none">
              <ol className="hv-steps">
                {[...shipment.events].reverse().map((event) => (
                  <li key={event.id} className="hv-step" data-kind={event.kind}>
                    <span className="hv-step__title">
                      {formatStamp(event.at, locale)} · {t(kindKey(event.kind))}
                      <span className="hv-micro"> · {t(sourceKey(event.source))}</span>
                    </span>
                    {event.note && <span className="hv-micro">{event.note}</span>}
                  </li>
                ))}
              </ol>
            </div>
            {mayPack && !closed && (
              <form
                className="hv-panel__foot"
                onSubmit={(event) => {
                  event.preventDefault()
                  void post(`/logistics/shipments/${shipment.id}/events`, {
                    kind,
                    note: note.trim() || null,
                  })
                  setNote('')
                }}
              >
                <select
                  aria-label={t('lg.record.kind')}
                  value={kind}
                  onChange={(event) => setKind(event.target.value as EventKind)}
                >
                  {EVENT_KINDS.map((value) => (
                    <option key={value} value={value}>
                      {t(kindKey(value))}
                    </option>
                  ))}
                </select>
                <input
                  aria-label={t('lg.record.note')}
                  placeholder={t('lg.record.note')}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
                <button className="hv-btn hv-btn--sm hv-btn--primary" type="submit">
                  {t('lg.action.record')}
                </button>
              </form>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
