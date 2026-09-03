'use client'

import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

import { cn } from '../../utils'

export function Toast({ toast }: ToastProps) {
  const [visible, setVisible] = useState(false)
  const [current, setCurrent] = useState(toast)

  useEffect(() => {
    if (toast) {
      setCurrent(toast)
      setVisible(true)
    } else {
      setVisible(false)
      const timer = setTimeout(() => setCurrent(null), 200)
      return () => clearTimeout(timer)
    }
  }, [toast])

  if (!current || typeof document === 'undefined') return null

  return createPortal(
    <div
      aria-live="polite"
      className={cn(
        'neo-toast fixed top-16 right-4 z-50 px-5 py-3.5 text-sm'
      )}
      data-tone={current.type}
      data-visible={visible}
      role="status"
    >
      {current.message}
    </div>,
    document.body
  )
}

interface ToastProps {
  toast: { message: string; type: 'error' | 'success' } | null
}
