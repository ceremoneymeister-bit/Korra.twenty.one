import { cn } from '../../utils'

const BASE_CN =
  'neo-badge inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium leading-none'

const TONE_CLASSES: Record<Exclude<Tone, 'default'>, string> = {
  destructive: '',
  outline: '',
  secondary: '',
  success: '',
  warning: ''
}

export const Badge = ({
  className,
  style,
  tone = 'default',
  ...props
}: BadgeProps) => {
  return (
    <span
      className={cn(
        BASE_CN,
        tone !== 'default' && TONE_CLASSES[tone],
        className
      )}
      data-tone={tone}
      style={style}
      {...(props as React.HTMLAttributes<HTMLSpanElement>)}
    />
  )
}

type Tone =
  | 'default'
  | 'destructive'
  | 'outline'
  | 'secondary'
  | 'success'
  | 'warning'

interface BadgeProps
  extends Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'> {
  tone?: Tone
}
