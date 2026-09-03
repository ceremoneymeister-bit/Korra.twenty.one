'use client'

import {
  Children,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode
} from 'react'

import { createPortal } from 'react-dom'

import { cn } from '../../utils'

import './form-controls.css'

const TRIGGER_CN =
  'neo-select-trigger flex h-11 min-h-11 w-full items-center justify-between gap-2 ' +
  'px-4 py-2 text-sm text-left cursor-pointer touch-manipulation'

/* Меню рендерится через портал в body с фиксированной позицией: внутри
 * заголовков и карточек с overflow оно обрезалось (владелец 03.09,
 * переключатель профиля в шапке раздела). */
const LISTBOX_CN =
  'nous-ui-select-menu fixed z-[70] max-h-60 overflow-auto origin-top ' +
  'neo-select-menu p-1.5'

type MenuState = 'closed' | 'closing' | 'open'

function cssTimeMs(name: string, fallback: number) {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  const value = Number.parseFloat(raw)
  if (!Number.isFinite(value)) return fallback
  return raw.endsWith('ms') ? value : raw.endsWith('s') ? value * 1000 : fallback
}

export function Select({
  children,
  className,
  disabled,
  id,
  onValueChange,
  placeholder,
  style,
  value
}: SelectProps) {
  const [menuState, setMenuState] = useState<MenuState>('closed')
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const closeTimerRef = useRef<number | null>(null)
  const [menuBox, setMenuBox] = useState<{ top: number; left: number; width: number } | null>(null)

  const measureMenu = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const gap = 6
    const estimated = 240
    const below = window.innerHeight - rect.bottom - gap
    const openUp = below < 160 && rect.top > below
    setMenuBox({
      top: openUp ? Math.max(8, rect.top - gap - Math.min(estimated, rect.top - gap)) : rect.bottom + gap,
      left: rect.left,
      width: Math.max(rect.width, 176),
    })
  }, [])
  const generatedId = useId()
  const triggerId = id ?? `nous-select-${generatedId}`
  const listboxId = `${triggerId}-listbox`

  const options = useMemo(() => collectOptions(children), [children])
  const selected = options.find(o => o.value === value)
  const selectedIndex = options.findIndex(o => o.value === value)
  const displayLabel = selected?.label ?? placeholder ?? value ?? ''
  const open = menuState === 'open'

  const close = useCallback(() => {
    setHighlightedIndex(-1)
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setMenuState('closed')
      return
    }

    setMenuState(current => (current === 'closed' ? current : 'closing'))
    closeTimerRef.current = window.setTimeout(() => {
      setMenuState('closed')
      closeTimerRef.current = null
    }, cssTimeMs('--dropdown-close-dur', 150))
  }, [])

  const openMenu = useCallback(
    (fromEnd = false) => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current)
        closeTimerRef.current = null
      }
      measureMenu()
      setMenuState('open')
      setHighlightedIndex(
        selectedIndex >= 0
          ? selectedIndex
          : fromEnd
            ? options.length - 1
            : options.length > 0
              ? 0
              : -1
      )
    },
    [options.length, selectedIndex]
  )

  useEffect(
    () => () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current)
      }
    },
    []
  )

  useEffect(() => {
    if (!open) return
    const ac = new AbortController()
    document.addEventListener(
      'mousedown',
      e => {
        const target = e.target as Node
        if (containerRef.current?.contains(target) || listRef.current?.contains(target)) return
        close()
      },
      { signal: ac.signal }
    )
    return () => ac.abort()
  }, [open, close])

  useEffect(() => {
    if (!open) return
    const onMove = () => measureMenu()
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open, measureMenu])

  useEffect(() => {
    if (!open || highlightedIndex < 0) return
    const el = listRef.current?.children[highlightedIndex] as
      | HTMLElement
      | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [open, highlightedIndex])

  const handleKeyDown = (e: KeyboardEvent) => {
    if (disabled) return
    switch (e.key) {
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (!open) {
          openMenu()
        } else if (highlightedIndex >= 0 && options[highlightedIndex]) {
          onValueChange?.(options[highlightedIndex].value)
          close()
        }
        break
      case 'ArrowDown':
        e.preventDefault()
        if (!open) {
          openMenu()
        } else {
          setHighlightedIndex(i => Math.min(i + 1, options.length - 1))
        }
        break
      case 'ArrowUp':
        e.preventDefault()
        if (!open) openMenu(true)
        else setHighlightedIndex(i => Math.max(i - 1, 0))
        break
      case 'Home':
        if (open) {
          e.preventDefault()
          setHighlightedIndex(0)
        }
        break
      case 'End':
        if (open) {
          e.preventDefault()
          setHighlightedIndex(options.length - 1)
        }
        break
      case 'Escape':
        if (open) {
          e.preventDefault()
          close()
        }
        break
      case 'Tab':
        if (open) close()
        break
    }
  }

  return (
    <div
      className={cn('relative', className)}
      ref={containerRef}
      style={style}
    >
      <button
        aria-activedescendant={
          open && highlightedIndex >= 0
            ? `${listboxId}-option-${highlightedIndex}`
            : undefined
        }
        aria-autocomplete="none"
        aria-controls={listboxId}
        aria-expanded={open}
        aria-haspopup="listbox"
        className={TRIGGER_CN}
        disabled={disabled}
        id={triggerId}
        onClick={() => !disabled && (open ? close() : openMenu())}
        onKeyDown={handleKeyDown}
        role="combobox"
        type="button"
      >
        <span className={cn('truncate', !selected && 'text-[var(--neo-text-secondary)]')}>
          {displayLabel}
        </span>

        <ChevronDownGlyph
          className={cn(
            'nous-ui-select-chevron size-3 shrink-0 text-[var(--neo-text-secondary)]',
            open && 'rotate-180'
          )}
        />
      </button>

      {menuState !== 'closed' && typeof document !== 'undefined' && createPortal(
        <div
          aria-hidden={menuState === 'closing' || undefined}
          className={LISTBOX_CN}
          data-state={menuState}
          id={listboxId}
          onAnimationEnd={() => {
            if (menuState === 'closing') setMenuState('closed')
          }}
          ref={listRef}
          role="listbox"
          style={menuBox ? { top: menuBox.top, left: menuBox.left, width: menuBox.width } : undefined}
        >
          {options.map((opt, i) => {
            const isSelected = opt.value === value
            const isHighlighted = i === highlightedIndex

            return (
              <div
                aria-selected={isSelected}
                className={cn(
                  'neo-select-option nous-ui-select-option flex min-h-10 cursor-pointer touch-manipulation items-center gap-2 px-3 py-2',
                  'text-sm',
                  isSelected && 'font-medium'
                )}
                data-highlighted={isHighlighted || undefined}
                id={`${listboxId}-option-${i}`}
                key={opt.value}
                onClick={() => {
                  onValueChange?.(opt.value)
                  close()
                }}
                onMouseDown={e => e.preventDefault()}
                onMouseEnter={() => setHighlightedIndex(i)}
                role="option"
              >
                <CheckGlyph
                  className={cn(
                    'nous-ui-select-check size-3 shrink-0 text-accent-line',
                    isSelected ? 'opacity-100' : 'opacity-0'
                  )}
                />
                <span className="truncate">{opt.label}</span>
              </div>
            )
          })}
        </div>,
        document.body
      )}
    </div>
  )
}

// Marker component — `Select` reads `value`/`children` from its tree.
// Renders nothing on its own.
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- Props are declarative data consumed by Select.
export function SelectOption(_props: SelectOptionProps) {
  return null
}

const ChevronDownGlyph = ({ className }: { className?: string }) => (
  <svg
    aria-hidden
    className={className}
    fill="none"
    stroke="currentColor"
    strokeLinecap="square"
    strokeWidth={1.5}
    viewBox="0 0 12 12"
  >
    <path d="M2.5 4.5 6 8l3.5-3.5" />
  </svg>
)

const CheckGlyph = ({ className }: { className?: string }) => (
  <svg
    aria-hidden
    className={className}
    fill="none"
    stroke="currentColor"
    strokeLinecap="square"
    strokeWidth={1.5}
    viewBox="0 0 12 12"
  >
    <path d="m2.5 6.5 2.5 2.5L9.5 3.5" />
  </svg>
)

function collectOptions(children: ReactNode): SelectOptionData[] {
  const out: SelectOptionData[] = []
  Children.forEach(children, child => {
    if (!isValidElement(child)) return
    const el = child as ReactElement<{
      children?: ReactNode
      value?: unknown
    }>
    if (el.props.value !== undefined) {
      out.push({
        label:
          typeof el.props.children === 'string'
            ? el.props.children
            : String(el.props.value),
        value: String(el.props.value)
      })
    } else if (el.props.children) {
      out.push(...collectOptions(el.props.children))
    }
  })
  return out
}

interface SelectOptionData {
  label: string
  value: string
}

interface SelectOptionProps {
  children: ReactNode
  value: string
}

interface SelectProps {
  children?: ReactNode
  className?: string
  disabled?: boolean
  id?: string
  onValueChange?: (value: string) => void
  placeholder?: string
  style?: CSSProperties
  value?: string
}
