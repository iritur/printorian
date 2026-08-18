import type { Locale } from '@printorian/ui'
import { OrdersScreen, translate, useSession } from '@printorian/ui'

import { ISSUE_REFUND, MANAGE_ORDER, OrderDesk } from './OrderDesk'

/**
 * The order desk: every order on the farm, and the controls to move one.
 *
 * The same `OrdersScreen` the customer cabinet uses, scoped to all orders and
 * with the desk under a selected row instead of a queue position.
 *
 * Advance and refund are still gated on permission even though this app is only
 * served on the farm LAN. Being unreachable from the internet is a deployment
 * fact, and deployment facts change; the API enforces the same permissions
 * regardless, and a button that would fail is worse than one that is absent.
 */
export function OrdersPage({ locale }: { locale: Locale }) {
  const { actor } = useSession()

  return (
    <OrdersScreen
      locale={locale}
      scope="all"
      title={translate(locale, 'orders.all.title')}
      renderDetail={(order, refresh) => (
        <OrderDesk
          order={order}
          locale={locale}
          mayAdvance={actor?.permissions.includes(MANAGE_ORDER) ?? false}
          mayRefund={actor?.permissions.includes(ISSUE_REFUND) ?? false}
          onChanged={refresh}
        />
      )}
    />
  )
}
