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
        'neo-switch peer relative inline-flex h-10 min-h-10 w-[60px] shrink-0 cursor-pointer touch-manipulation items-center rounded-full',
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
        className="neo-switch-track nous-ui-switch-track pointer-events-none absolute left-0 top-[5px] isolate h-[30px] w-[60px] overflow-hidden rounded-full"
      >
        <span
          aria-hidden
          className="neo-switch-thumb nous-ui-switch-thumb pointer-events-none absolute inset-y-0 left-0 h-full w-[200%] rounded-full"
        />
      </span>
    </button>
  )
})

interface SwitchProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}
