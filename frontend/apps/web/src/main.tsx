import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// The Harvester language first: it defines the `--hv-*` tokens and the
// component system. The app sheet follows so it can still override; it names
// `--hv-*` tokens directly now, but its selectors are still the pre-Harvester
// ones that Slice C converts.
import '@printorian/ui/harvester.css'
// The promo page's own section of the kit, loaded because this app builds it.
import '@printorian/ui/harvester-promo.css'
import '@printorian/ui/tokens.css'
import './app.css'
import { ErrorBoundary, applyRealm } from '@printorian/ui'
import { App, REALM } from './App'

// Before the first paint, so the ground is right on the very first frame rather
// than switching once React has mounted.
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
