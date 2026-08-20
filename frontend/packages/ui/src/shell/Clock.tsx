import { useEffect, useState } from 'react'

/** `19.08.2026 :: 13:05:19` — the kit's stamp, zero-padded throughout. */
function stamp(at: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${pad(at.getDate())}.${pad(at.getMonth() + 1)}.${at.getFullYear()}` +
    ` :: ${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
  )
}

/**
 * The running clock the kit ends every status strip with.
 *
 * Its own component for the same reason `StatusBar` keeps one: left inline in
 * `AppShell` the tick would put the whole application through a render every
 * second, and the chrome sits above every screen.
 *
 * `aria-hidden`, because a screen reader announcing the time once a second is
 * unusable and nothing here depends on hearing it.
 */
export function Clock() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <span className="hv-mono" aria-hidden="true">
      {stamp(now)}
    </span>
  )
}
