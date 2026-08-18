import { useEffect, useState } from 'react'

/** `13.08.2026 :: 19:44:07` — the kit's stamp, zero-padded throughout. */
function stamp(at: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${pad(at.getDate())}.${pad(at.getMonth() + 1)}.${at.getFullYear()}` +
    ` :: ${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
  )
}

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
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <footer className="hv-statusbar hv-panel__foot">
      <span>{note}</span>
      {/*
        `aria-hidden`: a screen reader announcing the time every second would be
        unusable, and nothing here depends on hearing it.
      */}
      <span className="hv-mono" aria-hidden="true">
        {stamp(now)}
      </span>
    </footer>
  )
}
