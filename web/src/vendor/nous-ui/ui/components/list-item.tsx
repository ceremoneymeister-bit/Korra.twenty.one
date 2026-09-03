'use client'

import { forwardRef, type ButtonHTMLAttributes } from 'react'

import { cn } from '../../utils'

export const ListItem = forwardRef<HTMLButtonElement, ListItemProps>(
  function ListItem(
    { active = false, children, className, type = 'button', ...props },
    ref
  ) {
    return (
      <button
        className={cn(
          'neo-list-item group relative flex w-full items-center gap-3 px-4 py-2.5 text-left',
          'text-sm cursor-pointer',
          className
        )}
        data-active={active || undefined}
        ref={ref}
        type={type}
        {...props}
      >
        {children}
      </button>
    )
  }
)

interface ListItemProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean
}
