import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const BACKEND = process.env.CHORUS_BACKEND_ORIGIN ?? 'http://localhost:8000'
const proxy = Object.fromEntries(
  ['/health', '/config', '/tools', '/agent', '/ingestion'].map((p) => [
    p,
    { target: BACKEND, changeOrigin: true },
  ]),
)

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': '/src' } },
  server: { port: 5173, strictPort: true, proxy },
  test: { environment: 'happy-dom', globals: true, setupFiles: ['./src/test/setup.ts'], passWithNoTests: true },
})
