import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useExplorerNodeStyles } from './useExplorerNodeStyles'
import { LIGHT_EXPLORER_NODE_STYLES, DARK_EXPLORER_NODE_STYLES } from '../lib/explorerElements'

const mockUseTheme = vi.fn()

vi.mock('@infra/ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@infra/ui')>()
  return { ...actual, useTheme: () => mockUseTheme() }
})

describe('useExplorerNodeStyles', () => {
  it('resolves the light palette when the theme resolves to light', () => {
    mockUseTheme.mockReturnValue({ mode: 'light', resolved: 'light', cycle: vi.fn() })
    const { result } = renderHook(() => useExplorerNodeStyles())
    expect(result.current).toBe(LIGHT_EXPLORER_NODE_STYLES)
  })

  it('resolves the dark palette when the theme resolves to dark', () => {
    mockUseTheme.mockReturnValue({ mode: 'dark', resolved: 'dark', cycle: vi.fn() })
    const { result } = renderHook(() => useExplorerNodeStyles())
    expect(result.current).toBe(DARK_EXPLORER_NODE_STYLES)
  })

  it('resolves the dark palette for system mode when the OS prefers dark', () => {
    mockUseTheme.mockReturnValue({ mode: 'system', resolved: 'dark', cycle: vi.fn() })
    const { result } = renderHook(() => useExplorerNodeStyles())
    expect(result.current).toBe(DARK_EXPLORER_NODE_STYLES)
  })
})
