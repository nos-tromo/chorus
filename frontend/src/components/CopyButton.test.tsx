import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { CopyButton } from './CopyButton'

// ── helpers ───────────────────────────────────────────────────────────────────

function mockClipboard(writeText: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    writable: true,
    configurable: true,
  })
}

function clearClipboard() {
  Object.defineProperty(navigator, 'clipboard', {
    value: undefined,
    writable: true,
    configurable: true,
  })
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('CopyButton', () => {
  afterEach(() => {
    vi.useRealTimers()
    clearClipboard()
  })

  it('renders with default aria-label "Copy"', () => {
    render(<CopyButton text="hello" />)
    const btn = screen.getByRole('button', { name: /copy/i })
    expect(btn).toBeTruthy()
  })

  it('calls navigator.clipboard.writeText with the given text on click', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    mockClipboard(writeText)

    render(<CopyButton text="hello" />)
    const btn = screen.getByRole('button', { name: /copy/i })

    await act(async () => {
      fireEvent.click(btn)
    })

    expect(writeText).toHaveBeenCalledWith('hello')
  })

  it('shows the copied label after click for ~1500ms then reverts', async () => {
    vi.useFakeTimers()

    const writeText = vi.fn().mockResolvedValue(undefined)
    mockClipboard(writeText)

    render(<CopyButton text="hello" copiedLabel="Copied!" />)
    const btn = screen.getByRole('button')
    expect(btn.getAttribute('title')).toBe('Copy')

    // Click and let the async handler settle
    await act(async () => {
      fireEvent.click(btn)
      // Flush the microtask queue (the resolved Promise from writeText)
      await Promise.resolve()
    })

    // Should now show copied state
    expect(btn.getAttribute('title')).toBe('Copied!')

    // Advance past 1500ms — should revert
    await act(async () => {
      vi.advanceTimersByTime(1600)
    })
    expect(btn.getAttribute('title')).toBe('Copy')
  })

  it('uses custom label and copiedLabel props', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    mockClipboard(writeText)

    render(<CopyButton text="hello" label="Kopieren" copiedLabel="Kopiert" />)
    expect(screen.getByRole('button', { name: 'Kopieren' })).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByRole('button'))
    })

    expect(screen.getByRole('button').getAttribute('title')).toBe('Kopiert')
  })

  it('does not throw when navigator.clipboard is absent', () => {
    clearClipboard()

    render(<CopyButton text="hello" />)
    const btn = screen.getByRole('button', { name: /copy/i })
    // Should not throw
    expect(() => fireEvent.click(btn)).not.toThrow()
  })
})
