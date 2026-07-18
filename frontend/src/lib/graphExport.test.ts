import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { toGraphJson, toGraphML, downloadText } from './graphExport'
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
    downloadText('synthetic.json', '{"a":1}', 'application/json')

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    const blobArg = createObjectURL.mock.calls[0][0] as Blob
    expect(blobArg).toBeInstanceOf(Blob)
    expect(blobArg.type).toBe('application/json')

    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
  })
})
