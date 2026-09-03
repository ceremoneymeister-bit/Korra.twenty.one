'use client'

import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react'
import { Checkbox as CheckboxPrimitive } from 'radix-ui'

import { cn } from '../../utils'

import './form-controls.css'

export const Checkbox = forwardRef<
  ElementRef<typeof CheckboxPrimitive.Root>,
  CheckboxProps
>(function Checkbox({ className, ...props }, ref) {
  return (
    <CheckboxPrimitive.Root
      className={cn(
        'nous-ui-checkbox peer relative flex h-10 min-h-10 w-10 shrink-0 cursor-pointer touch-manipulation items-center justify-center rounded-lg bg-transparent',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className
      )}
      ref={ref}
      {...props}
    >
      <span aria-hidden className="nous-ui-checkbox-frame pointer-events-none size-5 rounded-md border" />
      <CheckboxPrimitive.Indicator
        className="nous-ui-checkbox-indicator pointer-events-none absolute inset-0 flex items-center justify-center"
        forceMount
      >
        <svg
          aria-hidden
          className="size-3.5"
          fill="none"
          focusable="false"
          viewBox="0 0 12 12"
        >
          <path
            className="nous-ui-checkbox-path"
            d="m2.25 6.25 2.15 2.15 5.35-5.35"
            pathLength="1"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.75"
          />
        </svg>
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
})

type CheckboxProps = ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
