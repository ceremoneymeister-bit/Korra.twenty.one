import { cn } from '../../utils'

/**
 * Themeable card primitive. Themes can restyle every card by setting CSS
 * custom properties:
 *
 *   --component-card-clip-path
 *   --component-card-border-image
 *   --component-card-background
 *   --component-card-box-shadow
 *
 * All are optional — unset vars compute to their CSS initial value.
 */
const CARD_STYLE: React.CSSProperties = {
  background: 'var(--component-card-background, var(--neo-surface))',
  borderImage: 'var(--component-card-border-image, none)',
  boxShadow: 'var(--component-card-box-shadow, var(--neo-depth-3))',
  clipPath: 'var(--component-card-clip-path, none)'
}

export function Card({
  className,
  style,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'neo-card w-full',
        className
      )}
      style={{ ...CARD_STYLE, ...style }}
      {...props}
    />
  )
}

export function CardHeader({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'neo-card-header flex flex-col gap-2 p-5',
        className
      )}
      {...props}
    />
  )
}

export function CardTitle({
  className,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn(
        'text-base font-semibold',
        className
      )}
      {...props}
    />
  )
}

export function CardDescription({
  className,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={cn('neo-card-description font-mondwest text-xs', className)}
      {...props}
    />
  )
}

export function CardContent({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-5', className)} {...props} />
}
