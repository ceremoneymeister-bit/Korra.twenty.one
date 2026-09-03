'use client'

import {
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type ReactNode,
  useState
} from 'react'

import { cn } from '../../utils'

export function Tabs({ children, className, defaultValue }: TabsProps) {
  const [active, setActive] = useState(defaultValue)

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {children(active, setActive)}
    </div>
  )
}

export function TabsList({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'neo-tabs-list inline-flex h-10 items-center justify-start p-1',
        className
      )}
      {...props}
    />
  )
}

export function TabsTrigger({
  active,
  className,
  value: _value,
  ...props
}: TabsTriggerProps) {
  return (
    <button
      className={cn(
        'neo-tab relative inline-flex items-center justify-center whitespace-nowrap px-3 py-1.5',
        'font-mondwest text-display text-xs tracking-[0.1em] cursor-pointer',
        className
      )}
      data-active={active || undefined}
      type="button"
      {...props}
    />
  )
}

interface TabsProps {
  children: (active: string, setActive: (value: string) => void) => ReactNode
  className?: string
  defaultValue: string
}

interface TabsTriggerProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active: boolean
  value: string
}
