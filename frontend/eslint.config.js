import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  // `out/` is electron-vite's bundle output — built artifacts, not sources.
  { ignores: ['**/dist/**', '**/out/**', '**/generated/**', '**/node_modules/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // The backend is the source of domain types; `any` here means someone
      // stopped using the generated client (ADR-0005).
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'scripts/**'],
    rules: { '@typescript-eslint/no-non-null-assertion': 'off' },
  },
  {
    // Build tooling runs under Node, not in a browser.
    files: ['scripts/**/*.mjs', '*.config.{js,ts}', 'vitest.setup.ts'],
    languageOptions: {
      globals: { process: 'readonly', console: 'readonly', globalThis: 'readonly' },
    },
  },
)
