'use client'

import { type ButtonHTMLAttributes, forwardRef } from 'react'

import { cn } from '../../utils'

import './form-controls.css'

export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { checked, className, disabled, id, onCheckedChange, onClick, ...props },
  ref
) {
  return (
    <button
      {...props}
      aria-checked={checked}
      className={cn(
        'peer relative inline-flex h-10 min-h-10 w-12 shrink-0 cursor-pointer touch-manipulation items-center rounded-full bg-transparent',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className
      )}
      data-state={checked ? 'checked' : 'unchecked'}
      disabled={disabled}
      id={id}
      onClick={event => {
        onClick?.(event)
        if (!event.defaultPrevented) onCheckedChange(!checked)
      }}
      ref={ref}
      role="switch"
      type="button"
    >
      <span
        aria-hidden
        className={cn(
          'nous-ui-switch-track pointer-events-none absolute left-0.5 top-2 h-6 w-11 rounded-full border',
          checked
            ? 'border-primary/70 bg-primary'
            : 'border-input bg-muted'
        )}
      />
      <span
        aria-hidden
        className={cn(
          'nous-ui-switch-thumb pointer-events-none absolute left-1.5 top-3 size-4 rounded-full shadow-sm',
          checked
            ? 'translate-x-5 bg-primary-foreground'
            : 'translate-x-0 bg-background-base'
        )}
      />
    </button>
  )
})

interface SwitchProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}
