import { cn } from '../../utils'

import './form-controls.css'

export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'nous-ui-input flex h-10 min-h-10 w-full rounded-lg border border-input bg-background/60 px-3.5 py-2 font-courier text-sm text-midground shadow-sm',
        'placeholder:text-muted-foreground placeholder:opacity-100',
        'hover:border-primary/45',
        'focus-visible:border-ring focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        'aria-[invalid=true]:border-2 aria-[invalid=true]:border-destructive aria-[invalid=true]:focus-visible:outline-destructive',
        'read-only:bg-muted/30',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className
      )}
      {...props}
    />
  )
}
