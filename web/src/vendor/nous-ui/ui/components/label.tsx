import { cn } from '../../utils'

export function Label({
  className,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        'neo-label font-mondwest text-xs tracking-[0.1em] uppercase leading-none',
        className
      )}
      {...props}
    />
  )
}
