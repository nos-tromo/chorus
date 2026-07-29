import { useTheme } from '@infra/ui'
import {
  DARK_EXPLORER_NODE_STYLES,
  LIGHT_EXPLORER_NODE_STYLES,
} from '../lib/explorerElements'

/**
 * Resolves the AA-legible `ForceGraph` node palette for the *current*
 * theme (reactive to the toggle via `useTheme`'s `resolved` value) —
 * `ForceGraph` renders node/label colors as plain SVG `fill` attributes,
 * so the palette can't be expressed as a CSS custom property and must be
 * switched at render time instead.
 */
export function useExplorerNodeStyles() {
  const { resolved } = useTheme()
  return resolved === 'dark' ? DARK_EXPLORER_NODE_STYLES : LIGHT_EXPLORER_NODE_STYLES
}
