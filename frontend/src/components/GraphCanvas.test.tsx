import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { ElementDefinition, LayoutOptions, StylesheetStyle, StylesheetCSS } from 'cytoscape'

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

import { GraphCanvas } from './GraphCanvas'

// ── helpers ───────────────────────────────────────────────────────────────────

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

  it('renders a container div', () => {
    const { container } = render(
      <GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />,
    )
    const divs = container.querySelectorAll('div')
    expect(divs.length).toBeGreaterThan(0)
  })

  it('passes elements to the cytoscape factory', () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />)
    expect(capturedFactory).not.toBeNull()
    expect(capturedFactory!.elements).toEqual(ELEMENTS)
  })

  it('passes stylesheet to the cytoscape factory', () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />)
    expect(capturedFactory!.style).toEqual(STYLESHEET)
  })

  it('passes layout to the cytoscape factory', () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />)
    expect(capturedFactory!.layout).toEqual(LAYOUT)
  })

  it('renders a "Fit" button', () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />)
    const btn = screen.getByRole('button', { name: /fit/i })
    expect(btn).toBeTruthy()
  })

  it('fit button calls cy.fit()', () => {
    render(<GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />)
    const btn = screen.getByRole('button', { name: /fit/i })
    fireEvent.click(btn)
    expect(fitSpy).toHaveBeenCalledTimes(1)
  })

  it('calls cy.destroy() on unmount', () => {
    const { unmount } = render(
      <GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />,
    )
    unmount()
    expect(destroySpy).toHaveBeenCalledTimes(1)
  })

  it('re-inits cytoscape when elements change', () => {
    const newElements: ElementDefinition[] = [
      { data: { id: 'x', label: 'X' }, classes: 'topic' },
    ]

    const { rerender } = render(
      <GraphCanvas elements={ELEMENTS} stylesheet={STYLESHEET} layout={LAYOUT} />,
    )
    const firstCount = cytoscapeCallCount

    rerender(<GraphCanvas elements={newElements} stylesheet={STYLESHEET} layout={LAYOUT} />)

    expect(cytoscapeCallCount).toBeGreaterThan(firstCount)
  })
})
