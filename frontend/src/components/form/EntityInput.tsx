import { Input } from '@infra/ui'

export interface EntityInputProps {
  value: string
  onChange: (value: string) => void
  label: string
  placeholder?: string
  required?: boolean
}

export function EntityInput({
  value,
  onChange,
  label,
  placeholder,
  required,
}: EntityInputProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-foreground">
        {label}
        {required && <span className="ml-1 text-danger" aria-hidden>*</span>}
      </label>
      <Input
        type="text"
        value={value}
        placeholder={placeholder}
        required={required}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}
