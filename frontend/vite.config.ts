import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const BACKEND = process.env.CHORUS_BACKEND_ORIGIN ?? 'http://localhost:8000'
const API_PREFIXES = ['health', 'config', 'tools', 'agent', 'ingestion', 'stats', 'version']
const proxy = Object.fromEntries(
  API_PREFIXES.map((p) => [
    `/chorus/${p}`,
    { target: BACKEND, changeOrigin: true, rewrite: (path: string) => path.replace(/^\/chorus/, '') },
  ]),
)

export default defineConfig({
  base: '/chorus/',
  plugins: [react()],
  resolve: { alias: { '@': '/src' } },
  server: { port: 5173, strictPort: true, proxy },
  test: { environment: 'happy-dom', globals: true, setupFiles: ['./src/test/setup.ts'], passWithNoTests: true },
})
