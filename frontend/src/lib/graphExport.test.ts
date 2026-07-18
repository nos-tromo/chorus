import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { toGraphJson, toGraphML, toGraphHtml, downloadText } from './graphExport'
import type { ForceGraphEdge, ForceGraphNode } from '@infra/ui'

// ── fixture data (fully synthetic) ─────────────────────────────────────────

const NODES: ForceGraphNode[] = [
  { id: 'entity:e1', label: 'Synthetic Topic', kind: 'seed', size: 6 },
  { id: 'author:u1', label: 'Synthetic Author', kind: 'author', size: 2 },
]

const EDGES: ForceGraphEdge[] = [
  { source: 'author:u1', target: 'entity:e1', kind: 'mentions', weight: 3 },
]

describe('toGraphJson', () => {
  it('pretty-prints a {nodes, edges} passthrough with 2-space indent', () => {
    const json = toGraphJson(NODES, EDGES)
    expect(json).toBe(JSON.stringify({ nodes: NODES, edges: EDGES }, null, 2))
  })

  it('round-trips: parsing the output reproduces the input', () => {
    const json = toGraphJson(NODES, EDGES)
    expect(JSON.parse(json)).toEqual({ nodes: NODES, edges: EDGES })
  })

  it('is deterministic for the same input', () => {
    expect(toGraphJson(NODES, EDGES)).toBe(toGraphJson(NODES, EDGES))
  })

  it('handles empty graphs', () => {
    expect(toGraphJson([], [])).toBe(JSON.stringify({ nodes: [], edges: [] }, null, 2))
  })
})

describe('toGraphML', () => {
  it('emits a valid GraphML document with xml header and namespace', () => {
    const xml = toGraphML(NODES, EDGES)
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true)
    expect(xml).toContain('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">')
    expect(xml).toContain('</graphml>')
  })

  it('declares key attrs for node label/kind (string) and edge kind (string), weight (double)', () => {
    const xml = toGraphML(NODES, EDGES)
    expect(xml).toContain(
      '<key id="d_n_label" for="node" attr.name="label" attr.type="string"/>',
    )
    expect(xml).toContain('<key id="d_n_kind" for="node" attr.name="kind" attr.type="string"/>')
    expect(xml).toContain('<key id="d_e_kind" for="edge" attr.name="kind" attr.type="string"/>')
    expect(xml).toContain(
      '<key id="d_e_weight" for="edge" attr.name="weight" attr.type="double"/>',
    )
  })

  it('uses edgedefault="undirected" on the graph element', () => {
    const xml = toGraphML(NODES, EDGES)
    expect(xml).toContain('<graph edgedefault="undirected">')
  })

  it('emits one node element per node with id, label, kind data', () => {
    const xml = toGraphML(NODES, EDGES)
    expect(xml).toContain('<node id="entity:e1">')
    expect(xml).toContain('<node id="author:u1">')
  })

  it('emits edges with source/target and no directed attribute by default', () => {
    const xml = toGraphML(NODES, EDGES)
    expect(xml).toMatch(/<edge source="author:u1" target="entity:e1">/)
    expect(xml).not.toContain('directed="true"')
  })

  it('emits directed="true" only when the edge carries directed: true', () => {
    const directedEdges: ForceGraphEdge[] = [
      { source: 'author:u1', target: 'entity:e1', kind: 'follows', directed: true },
    ]
    const xml = toGraphML(NODES, directedEdges)
    expect(xml).toContain('<edge source="author:u1" target="entity:e1" directed="true">')
  })

  it('emits a weight data element only when weight is present', () => {
    const withWeight = toGraphML(NODES, EDGES)
    expect(withWeight).toContain('<data key="d_e_weight">3</data>')

    const noWeightEdges: ForceGraphEdge[] = [
      { source: 'author:u1', target: 'entity:e1', kind: 'follows' },
    ]
    const withoutWeight = toGraphML(NODES, noWeightEdges)
    expect(withoutWeight).not.toContain('d_e_weight')
  })

  it('XML-escapes &, <, >, ", \' in node labels and edge kinds', () => {
    const nastyNodes: ForceGraphNode[] = [
      { id: 'n1', label: `A & B <tag> "quoted" 'single'`, kind: 'seed' },
    ]
    const xml = toGraphML(nastyNodes, [])
    expect(xml).toContain(
      '&amp; B &lt;tag&gt; &quot;quoted&quot; &#39;single&#39;',
    )
    expect(xml).not.toMatch(/A & B/)
  })

  it('escapes special characters in node/edge ids used as XML attribute values', () => {
    const nodes: ForceGraphNode[] = [{ id: 'n"1', label: 'x', kind: 'seed' }]
    const xml = toGraphML(nodes, [])
    expect(xml).toContain('id="n&quot;1"')
  })

  it('escapes special characters like & in edge source and target attributes', () => {
    const nodes: ForceGraphNode[] = [
      { id: 'n1', label: 'x', kind: 'seed' },
      { id: 'n2', label: 'y', kind: 'seed' },
    ]
    const edges: ForceGraphEdge[] = [
      { source: 'entity&topic', target: 'author<name>', kind: 'mentions' },
    ]
    const xml = toGraphML(nodes, edges)
    expect(xml).toContain('<edge source="entity&amp;topic" target="author&lt;name&gt;">')
  })

  it('is deterministic for the same input', () => {
    expect(toGraphML(NODES, EDGES)).toBe(toGraphML(NODES, EDGES))
  })

  it('handles empty graphs', () => {
    const xml = toGraphML([], [])
    expect(xml).toContain('<graph edgedefault="undirected">')
    expect(xml).not.toContain('<node')
    expect(xml).not.toContain('<edge')
  })
})

describe('toGraphHtml', () => {
  const HTML_NODES: ForceGraphNode[] = [
    { id: 'entity:e1', label: 'Synthetic Topic', kind: 'seed', size: 6 },
    { id: 'author:u1', label: 'Synthetic Author', kind: 'author', size: 2 },
    { id: 'author:u2', label: 'Floating Author', kind: 'author', size: 1 },
  ]

  const HTML_EDGES: ForceGraphEdge[] = [
    { source: 'author:u1', target: 'entity:e1', kind: 'mentions', weight: 3 },
    { source: 'author:u1', target: 'author:u2', kind: 'follows', directed: true },
  ]

  const HTML_POSITIONS: Record<string, { x: number; y: number }> = {
    'entity:e1': { x: 0, y: 0 },
    'author:u1': { x: 100, y: 40 },
    // 'author:u2' deliberately omitted — missing-position edge case
  }

  const HTML_NODE_STYLES: Record<string, { color: string }> = {
    seed: { color: '#fbbf24' },
    author: { color: '#7c3aed' },
  }

  const HTML_EDGE_STYLES: Record<string, { dashed?: boolean; opacity?: number }> = {
    mentions: { opacity: 0.6 },
    follows: { dashed: true, opacity: 0.9 },
  }

  const baseOpts = {
    title: 'Synthetic Network — Climate',
    nodes: HTML_NODES,
    edges: HTML_EDGES,
    positions: HTML_POSITIONS,
    nodeStyles: HTML_NODE_STYLES,
    edgeStyles: HTML_EDGE_STYLES,
    legend: [
      { kind: 'seed', label: 'Seed' },
      { kind: 'author', label: 'Authors' },
    ],
  }

  it('returns one self-contained HTML document with a doctype and inline svg', () => {
    const html = toGraphHtml(baseOpts)
    expect(html.trim().toLowerCase().startsWith('<!doctype html>')).toBe(true)
    expect(html).toContain('<svg')
    expect(html).toContain(baseOpts.title)
    expect(html).toContain(`${HTML_NODES.length}`)
    expect(html).toContain(`${HTML_EDGES.length}`)
  })

  it('renders a legend chip per legend entry using the nodeStyles color', () => {
    const html = toGraphHtml(baseOpts)
    expect(html).toContain('Seed')
    expect(html).toContain('Authors')
    expect(html).toContain('#fbbf24')
    expect(html).toContain('#7c3aed')
  })

  it('places a circle at each baked position for nodes present in positions', () => {
    const html = toGraphHtml(baseOpts)
    // entity:e1 at (0,0) and author:u1 at (100,40) should each produce a circle
    // with matching cx/cy attributes.
    expect(html).toMatch(/<circle[^>]*cx="0"[^>]*cy="0"/)
    expect(html).toMatch(/<circle[^>]*cx="100"[^>]*cy="40"/)
  })

  it('still renders a node missing from positions (spiral fallback, not dropped)', () => {
    const html = toGraphHtml(baseOpts)
    // author:u2 has no baked position — it must still appear as a circle with
    // its label, just not at (0,0)/(100,40).
    expect(html).toContain('Floating Author')
    const circleCount = (html.match(/<circle/g) ?? []).length
    expect(circleCount).toBe(HTML_NODES.length)
  })

  it('HTML-escapes node labels containing </script> and &', () => {
    const nastyNodes: ForceGraphNode[] = [
      { id: 'n1', label: 'A & B </script><script>alert(1)</script>', kind: 'seed', size: 1 },
    ]
    const html = toGraphHtml({
      ...baseOpts,
      nodes: nastyNodes,
      edges: [],
      positions: { n1: { x: 10, y: 10 } },
    })
    expect(html).not.toContain('<script>alert(1)</script>')
    expect(html).toContain('&amp;')
    expect(html).toContain('&lt;/script&gt;')
  })

  it('has no external references (no http(s) URL, no src=, no href=)', () => {
    const html = toGraphHtml(baseOpts)
    expect(html).not.toMatch(/https?:\/\//i)
    expect(html).not.toMatch(/\bsrc=/i)
    expect(html).not.toMatch(/\bhref=/i)
  })

  it('emits stroke-dasharray for dashed edge kinds and a directed-edge arrow marker', () => {
    const html = toGraphHtml(baseOpts)
    expect(html).toMatch(/stroke-dasharray/)
    expect(html).toContain('marker-end')
    expect(html).toContain('<marker')
  })

  it('contains exactly one inline <script> block with no fetch/physics/expand logic', () => {
    const html = toGraphHtml(baseOpts)
    const scriptMatches = html.match(/<script(?![^>]*type="application\/json")[^>]*>/g) ?? []
    expect(scriptMatches.length).toBe(1)
    expect(html).not.toContain('fetch(')
  })

  it('is deterministic for the same input', () => {
    expect(toGraphHtml(baseOpts)).toBe(toGraphHtml(baseOpts))
  })
})

describe('downloadText', () => {
  let createObjectURL: ReturnType<typeof vi.fn>
  let revokeObjectURL: ReturnType<typeof vi.fn>
  let clickSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    createObjectURL = vi.fn(() => 'blob:mock-url')
    revokeObjectURL = vi.fn()
    ;(globalThis as any).URL.createObjectURL = createObjectURL
    ;(globalThis as any).URL.revokeObjectURL = revokeObjectURL
    clickSpy = vi.fn()
    const originalCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = originalCreateElement(tag)
      if (tag === 'a') (el as HTMLAnchorElement).click = clickSpy as () => void
      return el
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('creates a Blob object URL, triggers a download click, and revokes the URL', () => {
    vi.useFakeTimers()
    try {
      downloadText('synthetic.json', '{"a":1}', 'application/json')

      expect(createObjectURL).toHaveBeenCalledTimes(1)
      const blobArg = createObjectURL.mock.calls[0][0] as Blob
      expect(blobArg).toBeInstanceOf(Blob)
      expect(blobArg.type).toBe('application/json')

      expect(clickSpy).toHaveBeenCalledTimes(1)
      // revoke is deferred via setTimeout(..., 0), so run all timers
      expect(revokeObjectURL).not.toHaveBeenCalled()
      vi.runAllTimers()
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    } finally {
      vi.useRealTimers()
    }
  })
})
