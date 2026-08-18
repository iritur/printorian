import { useEffect, useState } from 'react'

import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate } from '@printorian/ui'

/**
 * Where a customer's order stands (scenario C7).
 *
 * The honesty rule from the planner, carried all the way to the person waiting:
 * a job queueing for a machine gets a place and a time; a job blocked on
 * something only a person can do gets a reason and **no date**. Showing a
 * comfortable estimate for the second kind is how a customer is quietly misled,
 * and it is the queue's version of V1 inventing telemetry.
 */

interface QueuePosition {
  job_status: string
  position: number | null
  reason: string | null
  predicted_start: string | null
  progress_percent: number | null
}

export function QueuePanel({ orderId, locale }: { orderId: string; locale: Locale }) {
  const t = (key: MessageKey) => translate(locale, key)
  const [queue, setQueue] = useState<QueuePosition | null>(null)
  const [asked, setAsked] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .get<QueuePosition | null>(`/orders/${orderId}/queue`)
      .then((result) => {
        if (!cancelled) setQueue(result)
      })
      // A missing queue is not an error worth showing: an order can simply have
      // no job yet. The panel stays quiet rather than reporting a fault.
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setAsked(true)
      })
    return () => {
      cancelled = true
    }
  }, [orderId])

  if (!asked || queue === null) return null

  if (queue.job_status === 'printing') {
    return (
      <p className="queue">
        {t('queue.printing')}
        {queue.progress_percent !== null && ` · ${queue.progress_percent}%`}
      </p>
    )
  }

  if (!queue.reason) return null

  // Falls back through the code's prefixes the same way error codes do, so a
  // wait-list reason added later renders as something rather than a blank.
  const reason = translate(locale, `queue.${queue.reason}` as MessageKey)

  return (
    <div className="queue">
      <strong>{t('queue.title')}</strong>
      <p>{reason}</p>
      {queue.position !== null && (
        <p>{translate(locale, 'queue.position', { position: queue.position })}</p>
      )}
      <p>
        {queue.predicted_start
          ? translate(locale, 'queue.predicted_start', {
              time: new Date(queue.predicted_start).toLocaleString(locale),
            })
          : // The distinction the whole feature exists for.
            t('queue.no_estimate')}
      </p>
    </div>
  )
}
