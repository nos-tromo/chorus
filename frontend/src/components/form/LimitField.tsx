import { Input } from '@infra/ui'

export interface LimitFieldProps {
  min: number
  max: number
  value: number
  onChange: (value: number) => void
  label: string
}

export function LimitField({ min, max, value, onChange, label }: LimitFieldProps) {
  function handleChange(raw: string) {
    const parsed = parseInt(raw, 10)
    if (isNaN(parsed)) return
    const clamped = Math.min(Math.max(parsed, min), max)
    onChange(clamped)
  }

  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-foreground">{label}</label>
      <Input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => handleChange(e.target.value)}
      />
    </div>
  )
}
