import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Overridable so a second dev server can run beside the usual one — against a
// staging API, or against a spare port while the normal one is occupied. The
// defaults are the everyday setup, so `npm run dev` is unchanged.
const API_TARGET = process.env.PRINTORIAN_API_URL ?? 'http://localhost:8000'
const WEB_PORT = Number(process.env.PRINTORIAN_WEB_PORT ?? 5173)

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly. Vite's default resolves to the IPv6 loopback only, so
    // `http://127.0.0.1:5173` is refused and tooling that prefers IPv4 (curl,
    // PowerShell, and later the Electron shell) cannot reach the dev server.
    host: '127.0.0.1',
    port: WEB_PORT,
    // The API is same-origin in production behind the reverse proxy (ADR-0003);
    // in development the proxy stands in for it so cookies behave identically.
    proxy: {
      // `ws: true` so `/api/ws/events` is proxied as a WebSocket upgrade rather
      // than answered with a 404. Routing the socket through the same `/api`
      // prefix keeps it same-origin, which is what makes the httpOnly session
      // cookie travel with the handshake — a browser cannot set an
      // Authorization header on a WebSocket.
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
