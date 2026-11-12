import React, { useCallback, useEffect, useRef, useState } from 'react';
import { MediaItem } from '../types';

// How long the cursor rests on a tile, or a finger holds it, before the preview opens.
const OPEN_DELAY_MS = 500;
// A finger that moves further than this is scrolling, not holding.
const HOLD_SLOP_PX = 10;

export interface Preview {
  item: MediaItem;
  anchor: DOMRect;
  isTouch: boolean;
}

/**
 * Hover (mouse) or press and hold (touch) a tile to see it larger. Tap and click still open the
 * viewer: a hold that opened the preview swallows the click that follows it.
 */
export const useHoverPreview = () => {
  const [preview, setPreview] = useState<Preview | null>(null);
  const timer = useRef<number | null>(null);
  const holdStart = useRef<{ x: number; y: number } | null>(null);
  const swallowClick = useRef(false);

  const cancelTimer = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };

  const close = useCallback(() => {
    cancelTimer();
    holdStart.current = null;
    setPreview(null);
  }, []);

  // Scrolling moves the tile away from the popup, so the popup goes.
  useEffect(() => {
    if (!preview) return;
    window.addEventListener('scroll', close, { passive: true });
    return () => window.removeEventListener('scroll', close);
  }, [preview, close]);

  useEffect(() => cancelTimer, []);

  const tileHandlers = (item: MediaItem) => ({
    onPointerEnter: (event: React.PointerEvent<HTMLElement>) => {
      if (event.pointerType !== 'mouse') return;
      const anchor = event.currentTarget.getBoundingClientRect();
      cancelTimer();
      timer.current = window.setTimeout(() => setPreview({ item, anchor, isTouch: false }), OPEN_DELAY_MS);
    },
    onPointerLeave: (event: React.PointerEvent<HTMLElement>) => {
      if (event.pointerType === 'mouse') close();
    },
    onPointerDown: (event: React.PointerEvent<HTMLElement>) => {
      if (event.pointerType === 'mouse') {
        // Clicking opens the viewer, the preview would only sit on top of it.
        close();
        return;
      }
      const anchor = event.currentTarget.getBoundingClientRect();
      holdStart.current = { x: event.clientX, y: event.clientY };
      cancelTimer();
      timer.current = window.setTimeout(() => {
        swallowClick.current = true;
        setPreview({ item, anchor, isTouch: true });
      }, OPEN_DELAY_MS);
    },
    onPointerMove: (event: React.PointerEvent<HTMLElement>) => {
      const start = holdStart.current;
      if (!start || event.pointerType === 'mouse') return;
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > HOLD_SLOP_PX) close();
    },
    onPointerUp: (event: React.PointerEvent<HTMLElement>) => {
      if (event.pointerType !== 'mouse') close();
    },
    onPointerCancel: close,
    // The phone's own long press menu (save image, share) would cover the preview.
    onContextMenu: (event: React.MouseEvent) => event.preventDefault(),
  });

  /** Call first in the tile's onClick: true means the click ended a hold and should do nothing. */
  const consumeHoldClick = () => {
    const swallowed = swallowClick.current;
    swallowClick.current = false;
    return swallowed;
  };

  return { preview, close, tileHandlers, consumeHoldClick };
};
