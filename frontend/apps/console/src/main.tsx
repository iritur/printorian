import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// The Harvester language first: it defines the `--hv-*` tokens and the
// component system. The app sheet follows so it can still override, and
// still carries `--pr-*` rules that Slice B is in the middle of retiring.
import '@printorian/ui/harvester.css'
import '@printorian/ui/tokens.css'
import './console.css'
import { applyRealm } from '@printorian/ui'
import { App, REALM } from './App'

// Before the first paint, so the hazard rail is present on the sign-in door as
// well as behind it. The shell re-applies it; this is what covers the screens
// drawn outside the shell.
applyRealm(REALM)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
