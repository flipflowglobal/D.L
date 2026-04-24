/**
 * useApi — generic data-fetching hook with auto-refresh.
 *
 * const { data, loading, error, refresh } = useApi(() => api.health(), 5000)
 */
import { useCallback, useEffect, useRef, useState } from 'react'

interface UseApiState<T> {
  data: T | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useApi<T>(
  fetcher: () => Promise<T>,
  intervalMs = 0,   // 0 = no auto-refresh
): UseApiState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await fetcherRef.current()
      setData(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    if (intervalMs > 0) {
      timerRef.current = setInterval(refresh, intervalMs)
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [refresh, intervalMs])

  return { data, loading, error, refresh }
}
