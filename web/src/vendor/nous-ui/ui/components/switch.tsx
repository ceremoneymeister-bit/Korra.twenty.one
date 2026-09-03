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
        'neo-switch peer relative inline-flex h-10 min-h-10 w-14 shrink-0 cursor-pointer touch-manipulation items-center rounded-full',
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
          'neo-switch-track nous-ui-switch-track pointer-events-none absolute left-0.5 top-1.5 h-7 w-13 rounded-full'
        )}
      />
      <span
        aria-hidden
        className={cn(
          'neo-switch-thumb nous-ui-switch-thumb pointer-events-none absolute left-1.5 top-2.5 size-5 rounded-full',
          checked ? 'translate-x-6' : 'translate-x-0'
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
