import type { Locale } from '@printorian/ui'
import { OrdersScreen, translate } from '@printorian/ui'

import { QueuePanel } from './QueuePanel'

/**
 * The customer cabinet — the scenario's step 9.
 *
 * A composition, not a screen of its own: the table, the detail panel and the
 * pinned breakdown are `OrdersScreen`, shared with the console's order desk. What
 * belongs *here* is the one thing a customer wants and staff do not — where their
 * order sits in the queue and when it is predicted to start.
 *
 * Deliberately no advance or refund controls. Those live in the console, which is
 * served from the farm's own server (ADR-0016) and never reaches a customer's
 * browser — so they are absent rather than hidden behind a permission check.
 */
export function CabinetPage({ locale }: { locale: Locale }) {
  return (
    <OrdersScreen
      locale={locale}
      scope="mine"
      title={translate(locale, 'cabinet.title')}
      renderDetail={(order) => <QueuePanel orderId={order.id} locale={locale} />}
    />
  )
}
