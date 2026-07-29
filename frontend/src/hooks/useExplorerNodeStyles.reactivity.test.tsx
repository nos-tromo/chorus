/**
 * Regression test for the in-tab reactivity bug fixed in @infra/ui v0.6.1:
 * before that fix, separate `useTheme()` call sites did not share state (no
 * shared store; only cross-tab `storage` events synced them), so toggling
 * the theme via `AppHeader` never re-rendered anything using
 * `useExplorerNodeStyles` — it stayed frozen on the mount-time palette,
 * silently reproducing the AA violation on the toggle path.
 *
 * Deliberately does NOT mock `@infra/ui`'s `useTheme` (unlike
 * `useExplorerNodeStyles.test.ts`, which mocks it to unit-test the
 * hook's own light/dark selection logic in isolation) — this test exists
 * to prove the *real* useTheme store propagates a toggle from one call
 * site to another.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useTheme } from '@infra/ui'
import { useExplorerNodeStyles } from './useExplorerNodeStyles'
import { DARK_EXPLORER_NODE_STYLES } from '../lib/explorerElements'

afterEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

// Sibling consumer: a real useTheme() instance, independent of the one
// useExplorerNodeStyles calls internally — exercises the toggle from a
// different call site, same as AppHeader vs. ToolExplorer/AgentGraphCard.
function ThemeToggle() {
  const { cycle } = useTheme()
  return (
    <button type="button" onClick={cycle}>
      toggle
    </button>
  )
}

function ExplorerPaletteProbe() {
  const styles = useExplorerNodeStyles()
  return <span data-testid="palette">{styles === DARK_EXPLORER_NODE_STYLES ? 'dark' : 'light'}</span>
}

describe('useExplorerNodeStyles reactivity (real @infra/ui useTheme, no mocking)', () => {
  it('switches the explorer palette when a sibling component cycles the theme', () => {
    render(
      <>
        <ThemeToggle />
        <ExplorerPaletteProbe />
      </>,
    )

    // Explicit-mode cycle order is system -> light -> dark -> system,
    // regardless of the test environment's OS preference, so two clicks
    // land deterministically on explicit dark.
    fireEvent.click(screen.getByRole('button', { name: 'toggle' }))
    expect(screen.getByTestId('palette')).toHaveTextContent('light')
    expect(document.documentElement.dataset.theme).toBe('light')

    fireEvent.click(screen.getByRole('button', { name: 'toggle' }))
    expect(screen.getByTestId('palette')).toHaveTextContent('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
