import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DataTable } from './DataTable'

// ─── column inference from mixed-key rows ─────────────────────────────────────

describe('DataTable – inferred columns', () => {
  it('collects keys across all rows in stable first-seen order', () => {
    const rows = [
      { a: 'one', b: 2 },
      { a: 'two', c: true }, // key "c" only appears from row 2 onward
    ]
    render(<DataTable rows={rows} empty="no results" />)

    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual(['a', 'b', 'c'])
  })

  it('renders scalar cells as text', () => {
    const rows = [{ name: 'Alice', score: 42, active: true }]
    render(<DataTable rows={rows} empty="no results" />)

    expect(screen.getByText('Alice')).toBeTruthy()
    expect(screen.getByText('42')).toBeTruthy()
    expect(screen.getByText('true')).toBeTruthy()
  })

  it('renders null cells as empty string', () => {
    const rows = [{ name: null }]
    render(<DataTable rows={rows} empty="no results" />)

    // the cell should exist (one td) but show empty text
    const cells = screen.getAllByRole('cell')
    expect(cells.some((c) => c.textContent === '')).toBe(true)
  })
})

// ─── object / array cells rendered as JSON ────────────────────────────────────

describe('DataTable – object / array cells', () => {
  it('renders an object cell as compact JSON', () => {
    const rows = [{ meta: { x: 1, y: 2 } }]
    render(<DataTable rows={rows} empty="no results" />)

    expect(screen.getByText('{"x":1,"y":2}')).toBeTruthy()
  })

  it('renders an array cell as compact JSON', () => {
    const rows = [{ tags: ['foo', 'bar'] }]
    render(<DataTable rows={rows} empty="no results" />)

    expect(screen.getByText('["foo","bar"]')).toBeTruthy()
  })
})

// ─── explicit columns ────────────────────────────────────────────────────────

describe('DataTable – explicit columns', () => {
  it('uses label as header and key as cell value', () => {
    const rows = [{ id: 1, name: 'Bob' }]
    const columns = [
      { key: 'id', label: 'ID' },
      { key: 'name', label: 'Full Name' },
    ]
    render(<DataTable rows={rows} columns={columns} empty="no results" />)

    expect(screen.getByText('ID')).toBeTruthy()
    expect(screen.getByText('Full Name')).toBeTruthy()
    expect(screen.getByText('1')).toBeTruthy()
    expect(screen.getByText('Bob')).toBeTruthy()
  })

  it('calls render fn when provided', () => {
    const rows = [{ score: 95 }]
    const columns = [
      {
        key: 'score',
        label: 'Score',
        render: (row: { score: number }) => <span data-testid="badge">{row.score}%</span>,
      },
    ]
    render(<DataTable rows={rows as any} columns={columns as any} empty="no results" />)

    expect(screen.getByTestId('badge').textContent).toBe('95%')
  })

  it('ignores keys not in columns when explicit columns given', () => {
    const rows = [{ a: 'shown', b: 'hidden' }]
    const columns = [{ key: 'a', label: 'A' }]
    render(<DataTable rows={rows} columns={columns} empty="no results" />)

    expect(screen.queryByText('b')).toBeNull()
    expect(screen.queryByText('hidden')).toBeNull()
  })
})

// ─── empty state ──────────────────────────────────────────────────────────────

describe('DataTable – empty state', () => {
  it('renders the empty prop when rows is empty', () => {
    render(<DataTable rows={[]} empty="Keine Ergebnisse gefunden" />)
    expect(screen.getByText('Keine Ergebnisse gefunden')).toBeTruthy()
  })

  it('does not render a table when rows is empty', () => {
    render(<DataTable rows={[]} empty="no data" />)
    expect(screen.queryByRole('table')).toBeNull()
  })
})
