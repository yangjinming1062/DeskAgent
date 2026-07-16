import { useCallback, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { appViewForPath, isOverlayView, NEW_CHAT_ROUTE } from '@/app/routes'

export function useOverlayRouting() {
  const location = useLocation()
  const navigate = useNavigate()

  const currentView = appViewForPath(location.pathname)
  const settingsOpen = currentView === 'settings'
  const chatOpen = currentView === 'chat'
  const overlayOpen = isOverlayView(currentView)

  const returnPathRef = useRef(NEW_CHAT_ROUTE)

  useEffect(() => {
    if (!overlayOpen) {
      returnPathRef.current = `${location.pathname}${location.search}${location.hash}`
    }
  }, [location.hash, location.pathname, location.search, overlayOpen])

  const closeOverlayToPreviousRoute = useCallback(
    () => navigate(returnPathRef.current || NEW_CHAT_ROUTE, { replace: true }),
    [navigate]
  )

  return {
    chatOpen,
    closeOverlayToPreviousRoute,
    currentView,
    settingsOpen
  }
}
