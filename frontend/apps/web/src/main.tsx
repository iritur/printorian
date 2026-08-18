import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// The Harvester language first: it defines the `--hv-*` tokens and the
// component system. The app sheet follows so it can still override, and
// still carries `--pr-*` rules that Slice B is in the middle of retiring.
import '@printorian/ui/harvester.css'
// The promo page's own section of the kit, loaded because this app builds it.
import '@printorian/ui/harvester-promo.css'
import '@printorian/ui/tokens.css'
import './app.css'
import { applyRealm } from '@printorian/ui'
import { App, REALM } from './App'

// Before the first paint, so the ground is right on the very first frame rather
// than switching once React has mounted.
applyRealm(REALM)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
