import { forwardRef, type TextareaHTMLAttributes } from 'react'

import { cn } from '../../utils'

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      className={cn(
        'neo-field min-h-24 w-full resize-y px-3.5 py-3 font-courier text-sm',
        className
      )}
      ref={ref}
      {...props}
    />
  )
})
