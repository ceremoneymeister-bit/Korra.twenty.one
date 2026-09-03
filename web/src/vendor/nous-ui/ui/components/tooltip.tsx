'use client'

import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react'
import { Tooltip as TooltipPrimitive } from 'radix-ui'

import { cn } from '../../utils'

export const TooltipProvider = TooltipPrimitive.Provider
export const Tooltip = TooltipPrimitive.Root
export const TooltipTrigger = TooltipPrimitive.Trigger

export const TooltipContent = forwardRef<
  ElementRef<typeof TooltipPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(function TooltipContent({ className, sideOffset = 8, ...props }, ref) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        className={cn(
          'neo-tooltip z-[100] max-w-64 px-3 py-2 text-xs leading-relaxed',
          className
        )}
        ref={ref}
        sideOffset={sideOffset}
        {...props}
      />
    </TooltipPrimitive.Portal>
  )
})
