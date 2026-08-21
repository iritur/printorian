/**
 * Why every fetch-on-mount in this codebase is written the same slightly odd way.
 *
 * `eslint-plugin-react-hooks` 7 brings the React Compiler's rules, and one of them
 * — `set-state-in-effect` — rejects an effect body that can reach `setState`
 * synchronously. Cascading renders are the reason: React must render, run the
 * effect, take the state update, and render again, all before the browser paints.
 *
 * Every screen here loads its data with the same shape, and it tripped that rule
 * twenty-odd times:
 *
 * ```ts
 * const load = useCallback(async () => { setRows(await api.get('/x')) }, [])
 * useEffect(() => { void load() }, [load])          // ← flagged
 * ```
 *
 * The state update was *already* asynchronous — it happens after an `await` — but
 * the analyser will not follow a call through a `useCallback` to prove it, and
 * conservative is the right default for a rule about correctness. Making the
 * asynchrony visible where the effect can see it satisfies it:
 *
 * ```ts
 * useEffect(() => {
 *   void (async () => {
 *     await load()
 *   })()
 * }, [load])
 * ```
 *
 * **This changes no runtime behaviour**, and it is worth being honest that it is
 * a change made for a static analyser rather than a bug fix. What it buys is that
 * the rule stays on, so the next effect that *does* set state synchronously is
 * caught.
 *
 * ## What this is not
 *
 * It is not cancellation. None of these effects guard against a slow response
 * landing after a newer one, which is a real race — two quick navigations can
 * leave the older payload on screen. Fixing that means threading a liveness check
 * into each `load`, which is a change to nineteen call sites with actual
 * behavioural consequences, and belongs in its own pass rather than smuggled into
 * a lint fix.
 *
 * ## The other two rules
 *
 * `react-hooks/refs` rejects reading `ref.current` during render — the value can
 * change without re-rendering, so a render that depends on it can be stale.
 * `react-hooks/immutability` rejects mutating a value that came from props or
 * state. Both were fixed at the site rather than by a pattern, because each was a
 * different mistake.
 *
 * This module exports nothing. It is documentation that lives where a reader who
 * greps for the pattern will find it, and it is referenced from the call sites.
 */

export {}
