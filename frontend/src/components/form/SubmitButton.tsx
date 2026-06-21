import { Button, Spinner } from '@infra/ui'

export interface SubmitButtonProps {
  loading: boolean
  disabled?: boolean
  children: React.ReactNode
}

export function SubmitButton({ loading, disabled, children }: SubmitButtonProps) {
  return (
    <Button
      type="submit"
      variant="primary"
      disabled={loading || disabled}
    >
      {loading ? (
        <span className="flex items-center gap-2">
          <Spinner />
          {children}
        </span>
      ) : (
        children
      )}
    </Button>
  )
}
