/**
 * Cytoscape stylesheets for the two graph screens.
 *
 * NOTE: Cytoscape renders to canvas — styles use literal CSS color values,
 * NOT Tailwind classes. This is expected and is NOT a design-token violation.
 * The hues mirror the app palette (violet accent, green, amber) so the two
 * rendering modes feel coherent.
 */

import type cytoscape from 'cytoscape'

type StylesheetBlock = cytoscape.StylesheetStyle | cytoscape.StylesheetCSS

// ── Network graph (network_around) ────────────────────────────────────────────
//
// Bipartite author-topic network. Authors → violet rounded rectangles.
// Topics → green ellipses. Seed node (the queried entity) → amber with thicker
// border. Edges carry a computed `width` from the mapper.

export const networkStylesheet: StylesheetBlock[] = [
  {
    selector: 'node',
    css: {
      label: 'data(label)',
      'text-valign': 'center',
      'text-halign': 'center',
      'font-size': 11,
      'text-wrap': 'wrap',
      'text-max-width': '120px',
      color: '#1e1b4b',
    },
  },
  {
    // Author nodes — violet rounded rectangles
    selector: 'node.author',
    css: {
      shape: 'round-rectangle',
      'background-color': '#7c3aed', // violet-600 hue ~hsl(262 83% 58%)
      color: '#ffffff',
      width: 120,
      height: 40,
      'font-size': 11,
    },
  },
  {
    // Topic nodes — green ellipses
    selector: 'node.topic',
    css: {
      shape: 'ellipse',
      'background-color': '#4ade80', // green-400
      color: '#14532d',
      width: 100,
      height: 36,
    },
  },
  {
    // Seed node overlay — amber fill + thicker border
    selector: 'node.seed',
    css: {
      'background-color': '#fbbf24', // amber-400
      color: '#451a03',
      'border-width': 3,
      'border-color': '#92400e',
    },
  },
  {
    // Edges — width from data, gray line
    selector: 'edge',
    css: {
      width: 'data(width)',
      'line-color': '#9e9e9e',
      'target-arrow-color': '#9e9e9e',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      label: 'data(weight)',
      'font-size': 9,
      color: '#757575',
    },
  },
  {
    // Highlighted nodes (click-to-highlight)
    selector: 'node.highlighted',
    css: {
      'border-width': 3,
      'border-color': '#7c3aed',
      'border-opacity': 1,
    },
  },
  {
    selector: 'node.dimmed',
    css: {
      opacity: 0.3,
    },
  },
  {
    selector: 'edge.dimmed',
    css: {
      opacity: 0.15,
    },
  },
]

// ── Social graph (social_network_around) ──────────────────────────────────────
//
// Ego network of follows / friends. All nodes are authors, coloured by ring:
//   seed (ring 0)  → amber
//   ring 1          → violet (accent)
//   ring 2          → slate
//   ringN (≥3)      → muted gray
//
// follows edges → directed (triangle arrowhead)
// friends edges → dashed line, no arrowhead

export const socialStylesheet: StylesheetBlock[] = [
  {
    selector: 'node',
    css: {
      label: 'data(label)',
      shape: 'ellipse',
      'text-valign': 'center',
      'text-halign': 'center',
      'font-size': 11,
      'text-wrap': 'wrap',
      'text-max-width': '120px',
    },
  },
  {
    // Seed author — amber
    selector: 'node.seed',
    css: {
      'background-color': '#fbbf24', // amber-400
      color: '#451a03',
      'border-width': 3,
      'border-color': '#92400e',
      width: 80,
      height: 80,
    },
  },
  {
    // Ring 1 — violet accent
    selector: 'node.ring1',
    css: {
      'background-color': '#7c3aed', // violet-600
      color: '#ffffff',
      width: 64,
      height: 64,
    },
  },
  {
    // Ring 2 — slate
    selector: 'node.ring2',
    css: {
      'background-color': '#64748b', // slate-500
      color: '#ffffff',
      width: 52,
      height: 52,
    },
  },
  {
    // Ring N (3+) — muted gray
    selector: 'node.ringN',
    css: {
      'background-color': '#b0bec5',
      color: '#263238',
      width: 44,
      height: 44,
    },
  },
  {
    // Follows edges — directed arrowhead
    selector: 'edge.follows',
    css: {
      'line-color': '#9e9e9e',
      'target-arrow-color': '#9e9e9e',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      width: 1.5,
      label: 'follows',
      'font-size': 9,
      color: '#757575',
    },
  },
  {
    // Friends edges — dashed, no arrowhead
    selector: 'edge.friends',
    css: {
      'line-color': '#9e9e9e',
      'target-arrow-shape': 'none',
      'line-style': 'dashed',
      'curve-style': 'bezier',
      width: 1.5,
      label: 'friends',
      'font-size': 9,
      color: '#757575',
    },
  },
  {
    selector: 'node.highlighted',
    css: {
      'border-width': 3,
      'border-color': '#fbbf24',
      'border-opacity': 1,
    },
  },
  {
    selector: 'node.dimmed',
    css: {
      opacity: 0.3,
    },
  },
  {
    selector: 'edge.dimmed',
    css: {
      opacity: 0.15,
    },
  },
]
