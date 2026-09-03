import { cva, type VariantProps } from 'class-variance-authority'
import { cloneElement } from 'react'

import { cn } from '../../utils'

import { Typography } from './typography'

const buttonVariants = cva(
  [
    'neo-button group relative grid cursor-pointer grid-cols-[auto_1fr_auto] items-center',
    'text-display leading-0 font-bold tracking-[0.2em]',
    'disabled:pointer-events-none'
  ],
  {
    defaultVariants: {
      destructive: false,
      ghost: false,
      invert: false,
      outlined: false,
      size: 'default'
    },
    variants: {
      destructive: { true: '' },
      ghost: { true: '' },
      invert: { true: '' },
      outlined: { true: '' },
      size: {
        default: 'min-h-11 px-[1.7em] py-[0.7em]',
        icon: 'p-2 aspect-square grid-cols-1 place-items-center [&>svg]:size-3.5',
        sm: 'px-3 py-1.5 text-[0.7rem] tracking-[0.15em] [&>svg]:size-3',
        xs: 'p-1 aspect-square grid-cols-1 place-items-center [&>svg]:size-3'
      }
    }
  }
)

const IconSlot = ({
  icon,
  side
}: {
  icon: React.ReactNode
  side: 'left' | 'right'
}) => (
  <>
    <span className="w-5" />

    <span
      className={cn(
        'absolute top-1/2 -translate-y-1/2',
        side === 'left' ? 'left-3' : 'right-3'
      )}
    >
      {typeof icon === 'object'
        ? cloneElement(icon as React.ReactElement<any>, {
            className: 'size-3.5'
          })
        : icon}
    </span>
  </>
)

export const Button = ({
  children,
  className,
  destructive,
  ghost,
  invert,
  outlined,
  prefix,
  size,
  suffix,
  ...props
}: ButtonProps) => {
  const neoVariant = destructive
    ? ghost
      ? 'destructive-ghost'
      : 'destructive'
    : ghost
      ? 'ghost'
      : outlined || invert
        ? 'neutral'
        : 'primary'

  return (
    <Typography
      as="button"
      className={cn(
        buttonVariants({ destructive, ghost, invert, outlined, size }),
        className
      )}
      data-neo-variant={neoVariant}
      mono
      {...props}
    >
      {prefix && <IconSlot icon={prefix} side="left" />}
      {children}
      {suffix && <IconSlot icon={suffix} side="right" />}
    </Typography>
  )
}

interface ButtonProps
  extends Omit<
      React.ButtonHTMLAttributes<HTMLButtonElement>,
      'prefix' | 'suffix'
    >,
    VariantProps<typeof buttonVariants> {
  prefix?: React.ReactNode
  suffix?: React.ReactNode
}
