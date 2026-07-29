import '@testing-library/jest-dom/vitest'

// Polyfill localStorage for happy-dom: vitest's happy-dom environment
// doesn't proxy `localStorage` onto globalThis (it's a happy-dom prototype
// getter, not an own property, and isn't in vitest's static global-key
// allowlist), so it falls through to Node's own experimental webstorage
// global, which is undefined without --localstorage-file. Same fix as
// @infra/ui's src/test/setup.ts.
if (!globalThis.localStorage) {
  const store: Record<string, string> = {}
  globalThis.localStorage = {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = String(value)
    },
    removeItem: (key: string) => {
      delete store[key]
    },
    clear: () => {
      Object.keys(store).forEach((key) => delete store[key])
    },
    length: 0,
    key: () => null,
  } as Storage
}
