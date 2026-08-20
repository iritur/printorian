import { Clock } from './Clock'

/**
 * The bar along the bottom of every screen.
 *
 * A wordmark and a running clock. The clock is not decoration on a farm: the
 * console is read from across a room, often on a machine nobody has touched for
 * hours, and "is this screen live or frozen?" is a real question. A second hand
 * answers it without anyone having to trust that the data is fresh.
 *
 * Its own component so the tick re-renders one line rather than the application.
 * Left inside `AppShell` it would put every screen through a render per second.
 */
export function StatusBar({ note }: { note: string }) {
  return (
    <footer className="hv-statusbar hv-panel__foot">
      <span>{note}</span>
      <Clock />
    </footer>
  )
}
