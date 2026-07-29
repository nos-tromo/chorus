import { BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer } from 'recharts'

interface PlatformCount {
  platform: string
  count: number
}

interface StatChartProps {
  data: PlatformCount[]
  noDataLabel?: string
}

/** Bar chart of posts per platform. Filters out entries with null/empty platform. */
export function StatChart({ data, noDataLabel = 'No platform data' }: StatChartProps) {
  const filtered = data.filter((d) => d.platform != null && d.platform !== '')

  if (filtered.length === 0) {
    return (
      <p className="py-4 text-center text-sm text-muted-foreground">{noDataLabel}</p>
    )
  }

  return (
    <div data-testid="stat-chart" className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={filtered} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
          <XAxis
            dataKey="platform"
            tick={{ fontSize: 12 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fontSize: 12 }}
            tickLine={false}
            axisLine={false}
            width={40}
          />
          <Tooltip />
          {/* #8b5cf6 clears the 3:1 non-text minimum against both the
              light (4.23:1) and dark (4.35:1) backgrounds — #6d28d9 fell
              to 2.59:1 on dark. */}
          <Bar dataKey="count" fill="#8b5cf6" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
