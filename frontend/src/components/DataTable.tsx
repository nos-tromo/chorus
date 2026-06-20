import type { ReactNode } from 'react'

export interface ColumnDef<T extends Record<string, unknown>> {
  key: string
  label: string
  render?: (row: T) => ReactNode
}

interface DataTableProps<T extends Record<string, unknown>> {
  rows: T[]
  columns?: ColumnDef<T>[]
  empty: string
}

/** Render a cell value: scalars as text, objects/arrays as compact JSON. */
function renderCell(value: unknown): ReactNode {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/**
 * Generic data table that either infers its columns from row keys (stable
 * first-seen order across all rows) or accepts an explicit `columns` array
 * with optional per-column render functions.
 *
 * Object / array cells are serialised as compact JSON, matching the old
 * Streamlit DataFrame dump. Scalar cells (string / number / boolean / null)
 * are rendered as plain text. When `rows` is empty, the `empty` string is
 * shown in place of the table.
 */
export function DataTable<T extends Record<string, unknown>>({
  rows,
  columns,
  empty,
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
    )
  }

  // Resolve column definitions — either explicit or inferred.
  const cols: ColumnDef<T>[] = columns ?? inferColumns(rows)

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted text-muted-foreground">
            {cols.map((col) => (
              <th key={col.key} className="px-4 py-2 text-left font-medium">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIdx) => (
            <tr
              key={rowIdx}
              className="border-b border-border last:border-0 hover:bg-accent/40"
            >
              {cols.map((col) => (
                <td key={col.key} className="px-4 py-2 tabular-nums">
                  {col.render ? col.render(row) : renderCell(row[col.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Collect all keys across every row in stable first-seen order. */
function inferColumns<T extends Record<string, unknown>>(rows: T[]): ColumnDef<T>[] {
  const seen = new Set<string>()
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      seen.add(key)
    }
  }
  return Array.from(seen).map((key) => ({ key, label: key }))
}
