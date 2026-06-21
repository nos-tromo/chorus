import { Input } from '@infra/ui'

export interface TimeRangeValue {
  from?: string
  to?: string
}

export interface TimeRangeInputsProps {
  value: TimeRangeValue
  onChange: (value: TimeRangeValue) => void
  fromLabel: string
  toLabel: string
  placeholder?: string
}

export function TimeRangeInputs({
  value,
  onChange,
  fromLabel,
  toLabel,
  placeholder = 'YYYY-MM-DDTHH:mm:ssZ',
}: TimeRangeInputsProps) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-foreground">{fromLabel}</label>
        <Input
          type="text"
          value={value.from ?? ''}
          placeholder={placeholder}
          onChange={(e) =>
            onChange({ ...value, from: e.target.value || undefined })
          }
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-foreground">{toLabel}</label>
        <Input
          type="text"
          value={value.to ?? ''}
          placeholder={placeholder}
          onChange={(e) =>
            onChange({ ...value, to: e.target.value || undefined })
          }
        />
      </div>
    </div>
  )
}
