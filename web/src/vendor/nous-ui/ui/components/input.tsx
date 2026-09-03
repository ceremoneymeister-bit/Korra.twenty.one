import { cn } from '../../utils'

import './form-controls.css'

export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'neo-field nous-ui-input flex h-11 min-h-11 w-full rounded-lg px-4 py-2 text-sm',
        className
      )}
      {...props}
    />
  )
}
