import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { renderHook, act } from '@testing-library/react'
import { EntityInput } from './EntityInput'
import { LimitField } from './LimitField'
import { SubmitButton } from './SubmitButton'
import { useToolForm } from './useToolForm'

// ─── EntityInput ─────────────────────────────────────────────────────────────

describe('EntityInput', () => {
  it('calls onChange with the new value when user types', () => {
    const onChange = vi.fn()
    render(
      <EntityInput label="Entity" value="" onChange={onChange} placeholder="Search…" />,
    )
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'Alice' } })
    expect(onChange).toHaveBeenCalledWith('Alice')
  })

  it('renders the label text', () => {
    render(<EntityInput label="My Label" value="" onChange={vi.fn()} />)
    expect(screen.getByText('My Label')).toBeTruthy()
  })

  it('reflects controlled value', () => {
    render(<EntityInput label="L" value="Bob" onChange={vi.fn()} />)
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('Bob')
  })
})

// ─── LimitField ──────────────────────────────────────────────────────────────

describe('LimitField', () => {
  it('calls onChange with the entered value when in range', () => {
    const onChange = vi.fn()
    render(<LimitField label="Limit" min={1} max={100} value={10} onChange={onChange} />)
    const input = screen.getByRole('spinbutton')
    fireEvent.change(input, { target: { value: '50' } })
    expect(onChange).toHaveBeenCalledWith(50)
  })

  it('clamps value below min to min', () => {
    const onChange = vi.fn()
    render(<LimitField label="Limit" min={5} max={100} value={10} onChange={onChange} />)
    const input = screen.getByRole('spinbutton')
    fireEvent.change(input, { target: { value: '0' } })
    expect(onChange).toHaveBeenCalledWith(5)
  })

  it('clamps value above max to max', () => {
    const onChange = vi.fn()
    render(<LimitField label="Limit" min={1} max={50} value={10} onChange={onChange} />)
    const input = screen.getByRole('spinbutton')
    fireEvent.change(input, { target: { value: '999' } })
    expect(onChange).toHaveBeenCalledWith(50)
  })

  it('renders the label text', () => {
    render(<LimitField label="Result limit" min={1} max={100} value={25} onChange={vi.fn()} />)
    expect(screen.getByText('Result limit')).toBeTruthy()
  })
})

// ─── SubmitButton ─────────────────────────────────────────────────────────────

describe('SubmitButton', () => {
  it('renders children when not loading', () => {
    render(<SubmitButton loading={false}>Run query</SubmitButton>)
    expect(screen.getByRole('button', { name: /run query/i })).toBeTruthy()
  })

  it('is disabled when loading', () => {
    render(<SubmitButton loading>Run query</SubmitButton>)
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows a spinner (accessible label) when loading', () => {
    render(<SubmitButton loading>Run</SubmitButton>)
    // Spinner renders role="status" with an aria-label or visible text
    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('is disabled when disabled prop is passed', () => {
    render(<SubmitButton loading={false} disabled>Run</SubmitButton>)
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true)
  })
})

// ─── useToolForm ──────────────────────────────────────────────────────────────

describe('useToolForm', () => {
  const initial = { entity: '', limit: 10, from: '' }

  it('initialises values from the initial object', () => {
    const { result } = renderHook(() => useToolForm(initial))
    expect(result.current.values).toEqual(initial)
  })

  it('set updates exactly one field immutably', () => {
    const { result } = renderHook(() => useToolForm(initial))
    act(() => {
      result.current.set('entity', 'Alice')
    })
    expect(result.current.values.entity).toBe('Alice')
    expect(result.current.values.limit).toBe(10)
    expect(result.current.values.from).toBe('')
  })

  it('reset restores initial values', () => {
    const { result } = renderHook(() => useToolForm(initial))
    act(() => {
      result.current.set('entity', 'Alice')
      result.current.set('limit', 99)
    })
    act(() => {
      result.current.reset()
    })
    expect(result.current.values).toEqual(initial)
  })
})
