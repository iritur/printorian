import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// The Harvester language first: it defines the `--hv-*` tokens and the
// component system. The app sheet follows so it can still override, and
// still carries `--pr-*` rules that Slice B is in the middle of retiring.
import '@printorian/ui/harvester.css'
import '@printorian/ui/tokens.css'
import './console.css'
import { ErrorBoundary, applyRealm } from '@printorian/ui'
import { App, REALM } from './App'

// Before the first paint, so the hazard rail is present on the sign-in door as
// well as behind it. The shell re-applies it; this is what covers the screens
// drawn outside the shell.
applyRealm(REALM)

// Outside `App`, so a throw while the shell itself is mounting is caught too —
// a boundary inside the tree it is meant to protect protects nothing.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
