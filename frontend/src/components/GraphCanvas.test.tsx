import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { ElementDefinition, LayoutOptions, StylesheetStyle, StylesheetCSS } from 'cytoscape'
import type { AppConfig } from '../api/types'

// ── mocks (must run before any component imports) ─────────────────────────────

vi.mock('../api/config', () => ({
  fetchConfig: vi.fn(
    (): Promise<AppConfig> =>
      Promise.resolve({ language: 'en', ingestion_enabled: false, version: '0.1.0' }),
  ),
}))

vi.mock('../api/client', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly detail: unknown,
    ) {
      super(`API ${status}`)
      this.name = 'ApiError'
    }
  },
  url: (path: string) => path,
}))

type StylesheetBlock = StylesheetStyle | StylesheetCSS

// ── mock cytoscape (must come before component import) ────────────────────────
//
// happy-dom cannot lay out canvas-based graphs. We mock the cytoscape factory
// to a lightweight stub that records calls and exposes spies for the assertions.

const fitSpy = vi.fn()
const destroySpy = vi.fn()
const onSpy = vi.fn()
const elementsSpy = vi.fn().mockReturnValue({ removeClass: vi.fn() })
const dollarSpy = vi.fn().mockReturnValue({
  neighborhood: vi.fn().mockReturnValue({ addClass: vi.fn() }),
  addClass: vi.fn(),
})

// The fake cy object returned by cytoscape(...)
const fakeCy = {
  fit: fitSpy,
  destroy: destroySpy,
  on: onSpy,
  elements: elementsSpy,
  $: dollarSpy,
}

// Captured factory args — populated each time the mock factory is called
let capturedFactory: {
  elements: ElementDefinition[]
  style: StylesheetBlock[]
  layout: LayoutOptions
} | null = null

// Track the mock call count for re-init assertions
let cytoscapeCallCount = 0

vi.mock('cytoscape', () => {
  const cytoscapeFactory = vi.fn((opts: {
    container: HTMLElement
    elements: ElementDefinition[]
    style: StylesheetBlock[]
    layout: LayoutOptions
    [key: string]: unknown
  }) => {
    capturedFactory = { elements: opts.elements, style: opts.style, layout: opts.layout }
    cytoscapeCallCount++
    return fakeCy
  })
  return { default: cytoscapeFactory }
})

import { ConfigProvider } from '../config/ConfigContext'
import { GraphCanvas } from './GraphCanvas'

// ── helpers ───────────────────────────────────────────────────────────────────

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ConfigProvider>{children}</ConfigProvider>
      </QueryClientProvider>
    )
  }
}


const ELEMENTS: ElementDefinition[] = [
  { data: { id: 'a', label: 'A' }, classes: 'author' },
  { data: { id: 'b', label: 'B' }, classes: 'topic' },
  { data: { id: 'ab', source: 'a', target: 'b', width: 2 } },
]

const STYLESHEET: StylesheetBlock[] = [{ selector: 'node', css: { 'background-color': '#fff' } }]

const LAYOUT: LayoutOptions = { name: 'cose' }

// ── tests ─────────────────────────────────────────────────────────────────────

describe('GraphCanvas', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    capturedFactory = null
    cytoscapeCallCount = 0
  })

  it('renders a container div', async () => {
    const { container } = render(
      <GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />,
      { wrapper: makeWrapper() },
    )
    await waitFor(() => {
      const divs = container.querySelectorAll('div')
      expect(divs.length).toBeGreaterThan(0)
    })
  })

  it('passes elements to the cytoscape factory', async () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />, {
      wrapper: makeWrapper(),
    })
    await waitFor(() => {
      expect(capturedFactory).not.toBeNull()
      expect(capturedFactory!.elements).toEqual(ELEMENTS)
    })
  })

  it('passes stylesheet to the cytoscape factory', async () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />, {
      wrapper: makeWrapper(),
    })
    await waitFor(() => {
      expect(capturedFactory!.style).toEqual(STYLESHEET)
    })
  })

  it('passes layout to the cytoscape factory', async () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />, {
      wrapper: makeWrapper(),
    })
    await waitFor(() => {
      expect(capturedFactory!.layout).toEqual(LAYOUT)
    })
  })

  it('renders a "Fit" button', async () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />, {
      wrapper: makeWrapper(),
    })
    const btn = await screen.findByRole('button', { name: /fit/i })
    expect(btn).toBeTruthy()
  })

  it('fit button calls cy.fit()', async () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />, {
      wrapper: makeWrapper(),
    })
    const btn = await screen.findByRole('button', { name: /fit/i })
    fireEvent.click(btn)
    expect(fitSpy).toHaveBeenCalledTimes(1)
  })

  it('calls cy.destroy() on unmount', async () => {
    const { unmount } = render(
      <GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />,
      { wrapper: makeWrapper() },
    )
    // Wait until the component is fully rendered (config loaded, cy initialised)
    await waitFor(() => {
      expect(capturedFactory).not.toBeNull()
    })
    unmount()
    expect(destroySpy).toHaveBeenCalledTimes(1)
  })

  it('re-inits cytoscape when elements change', async () => {
    const newElements: ElementDefinition[] = [
      { data: { id: 'x', label: 'X' }, classes: 'topic' },
    ]
    const wrapper = makeWrapper()
    const { rerender } = render(
      <GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />,
      { wrapper },
    )
    // Wait for initial render
    await waitFor(() => {
      expect(capturedFactory).not.toBeNull()
    })
    const firstCount = cytoscapeCallCount

    rerender(<GraphCanvas elements={newElements} stylesheet={STYLESHEET} layout={LAYOUT} />)

    await waitFor(() => {
      expect(cytoscapeCallCount).toBeGreaterThan(firstCount)
    })
  })
})
