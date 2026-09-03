'use client'

import {
  type CSSProperties,
  type HTMLAttributes
} from 'react'
import type { BrailleSpinnerName } from 'unicode-animations'

import { cn } from '../../utils'

/**
 * Compact loading activity. `name` remains accepted for API compatibility;
 * Korra intentionally renders one consistent depth-based motion language.
 */
export function Spinner({
  className,
  name = 'braille',
  style,
  ...props
}: SpinnerProps) {
  return (
    <span
      aria-hidden={props['aria-label'] ? undefined : true}
      className={cn('neo-spinner leading-none', className)}
      data-spinner-name={name}
      style={style}
      {...props}
    >
      <span aria-hidden className="neo-spinner-dot" />
      <span aria-hidden className="neo-spinner-dot" />
      <span aria-hidden className="neo-spinner-dot" />
    </span>
  )
}

/** Block-loading placeholder; callers choose its dimensions with className. */
export function Skeleton({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn('neo-skeleton min-h-4 w-full', className)}
      {...props}
    />
  )
}

interface SpinnerProps extends HTMLAttributes<HTMLSpanElement> {
  className?: string
  name?: BrailleSpinnerName
  style?: CSSProperties
}
