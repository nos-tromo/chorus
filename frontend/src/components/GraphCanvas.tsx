/**
 * GraphCanvas — thin React wrapper around the Cytoscape graph library.
 *
 * Renders a Cytoscape instance into a fixed-height div. Re-initialises
 * (destroy + re-create) when `elements` change. Exposes a "Fit" button and
 * click-to-highlight-neighbourhood interaction.
 *
 * NOTE: Cytoscape operates on a canvas; it cannot be sized by Tailwind layout
 * classes alone. The inner container div gets an explicit `h-[600px]` so the
 * canvas has non-zero dimensions to render into.
 */

import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import type { ElementDefinition, LayoutOptions, StylesheetStyle, StylesheetCSS } from 'cytoscape'

type StylesheetBlock = StylesheetStyle | StylesheetCSS

export interface GraphCanvasProps {
  elements: ElementDefinition[]
  stylesheet: StylesheetBlock[]
  layout: LayoutOptions
}

export function GraphCanvas({ elements, stylesheet, layout }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Hold the cy instance so the fit button and cleanup can reference it
  const cyRef = useRef<cytoscape.Core | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    // Destroy any previous instance before creating a new one
    if (cyRef.current) {
      cyRef.current.destroy()
      cyRef.current = null
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: stylesheet,
      layout,
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    })

    cyRef.current = cy

    // ── Click-to-highlight ────────────────────────────────────────────────────
    // Click a node → highlight it + its neighbourhood; click background → reset.
    cy.on('tap', 'node', (evt) => {
      const node = evt.target as cytoscape.NodeSingular
      cy.elements().removeClass('highlighted dimmed')
      const neighbourhood = node.closedNeighborhood()
      cy.elements().not(neighbourhood).addClass('dimmed')
      neighbourhood.addClass('highlighted')
    })

    cy.on('tap', (evt) => {
      if (evt.target === cy) {
        // Background tap — clear all highlights
        cy.elements().removeClass('highlighted dimmed')
      }
    })

    return () => {
      cy.destroy()
      cyRef.current = null
    }
    // Re-run when elements change (stylesheet and layout are stable refs per render)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements])

  function handleFit() {
    cyRef.current?.fit()
  }

  return (
    <div className="relative w-full">
      {/* Cytoscape canvas target */}
      <div ref={containerRef} className="h-[600px] w-full rounded border border-border" />
      {/* Controls overlay */}
      <div className="absolute top-2 right-2 flex gap-1">
        <button
          type="button"
          onClick={handleFit}
          className="rounded bg-surface px-2 py-1 text-xs font-medium text-foreground shadow hover:bg-muted"
        >
          Fit
        </button>
      </div>
    </div>
  )
}
