import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    // Vitest replaces every CSS import with an empty string, which is right for
    // a component test — nothing here asserts on a computed style, and paying
    // PostCSS on every render would be waste. But it also empties `?raw`, and
    // `tokens.test.ts` reads the stylesheets as text to enforce the design
    // kit's exit criterion. Narrowing the exception to the `?raw` query buys
    // that one back without turning the pipeline on for ordinary imports.
    css: { include: [/\?raw$/] },
    setupFiles: ['./vitest.setup.ts'],
    include: ['packages/**/*.test.{ts,tsx}', 'apps/**/*.test.{ts,tsx}'],
  },
})
