import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The console is served by the farm's own server on the LAN (ADR-0016), so it is
// same-origin with the API in production exactly as the storefront is behind the
// tunnel. The dev proxy stands in for that, so the session cookie behaves the
// same in both.
const API_TARGET = process.env.PRINTORIAN_API_URL ?? 'http://localhost:8000'
// A different port from the storefront's 5173 so both can run at once — which is
// the normal case now that they are two apps rather than two tabs of one.
const CONSOLE_PORT = Number(process.env.PRINTORIAN_CONSOLE_PORT ?? 5174)

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly. Vite's default resolves to the IPv6 loopback only, so
    // `http://127.0.0.1:5174` is refused and tooling that prefers IPv4 cannot
    // reach the dev server.
    host: '127.0.0.1',
    port: CONSOLE_PORT,
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
