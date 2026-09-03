import { cn } from '../../utils'

import './form-controls.css'

export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'neo-field nous-ui-input flex h-10 min-h-10 w-full rounded-lg px-3.5 py-2 font-courier text-sm',
        className
      )}
      {...props}
    />
  )
}
