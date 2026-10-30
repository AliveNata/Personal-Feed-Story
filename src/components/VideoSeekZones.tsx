import React, { useEffect, useRef } from 'react';
import { ChevronsLeft, ChevronsRight } from 'lucide-react';

export const SEEK_STEP_SECONDS = 5;
// Taps closer together than this count as one double tap, and keep adding 5 seconds.
const DOUBLE_TAP_MS = 300;
// Room left at the bottom for the native seek bar and buttons.
const CONTROLS_HEIGHT = 'calc(100% - 4.5rem)';

type Side = 'back' | 'forward';

export interface SeekFlash {
  side: Side;
  seconds: number;
  key: number;
}

interface VideoSeekZonesProps {
  onSeek: (seconds: number) => void;
  onTogglePlay: () => void;
  onToggleFullscreen: () => void;
  flash: SeekFlash | null;
}

/**
 * Left and right thirds over a video, the way phone players work: double tap on the left goes
 * back 5 seconds, on the right forward, each extra tap adds 5. A single tap plays or pauses.
 * With a mouse, double click toggles full screen, the desktop convention.
 * The middle and the bottom stay free, so the native controls keep working there.
 */
const VideoSeekZones: React.FC<VideoSeekZonesProps> = ({
  onSeek,
  onTogglePlay,
  onToggleFullscreen,
  flash,
}) => {
  const lastTap = useRef<{ side: Side; at: number } | null>(null);
  const downAt = useRef<{ x: number; y: number } | null>(null);
  const singleTapTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (singleTapTimer.current) window.clearTimeout(singleTapTimer.current);
    },
    [],
  );

  const handleTap = (side: Side) => (event: React.PointerEvent) => {
    // Pans and pinches start on these zones too, only a tap counts here.
    const start = downAt.current;
    downAt.current = null;
    if (!event.isPrimary || !start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 10) {
      return;
    }
    const now = performance.now();
    const previous = lastTap.current;
    const isRepeat = previous !== null && now - previous.at < DOUBLE_TAP_MS;
    lastTap.current = { side, at: now };

    if (isRepeat) {
      if (singleTapTimer.current) window.clearTimeout(singleTapTimer.current);
      singleTapTimer.current = null;
      if (event.pointerType === 'mouse') {
        lastTap.current = null;
        onToggleFullscreen();
        return;
      }
      onSeek(side === 'back' ? -SEEK_STEP_SECONDS : SEEK_STEP_SECONDS);
      return;
    }
    // Wait to see whether a second tap follows before treating it as play or pause.
    singleTapTimer.current = window.setTimeout(() => {
      singleTapTimer.current = null;
      onTogglePlay();
    }, DOUBLE_TAP_MS);
  };

  const zone = (side: Side) => (
    <div
      data-seek-zone
      onPointerDown={(event) => {
        downAt.current = { x: event.clientX, y: event.clientY };
      }}
      onPointerUp={handleTap(side)}
      className={`absolute top-0 w-[35%] select-none ${side === 'back' ? 'left-0' : 'right-0'}`}
      style={{ height: CONTROLS_HEIGHT, WebkitTapHighlightColor: 'transparent' }}
    >
      {flash && flash.side === side && (
        <div
          key={flash.key}
          role="status"
          className={`pointer-events-none absolute inset-y-0 flex w-full items-center justify-center bg-white/10 animate-seek-flash motion-reduce:animate-none ${
            side === 'back' ? 'rounded-r-full' : 'rounded-l-full'
          }`}
        >
          <span className="flex flex-col items-center gap-1 text-sm font-semibold text-white drop-shadow">
            {side === 'back' ? (
              <ChevronsLeft className="h-8 w-8" aria-hidden="true" />
            ) : (
              <ChevronsRight className="h-8 w-8" aria-hidden="true" />
            )}
            {side === 'back' ? '-' : '+'}
            {flash.seconds} detik
          </span>
        </div>
      )}
    </div>
  );

  return (
    <>
      {zone('back')}
      {zone('forward')}
    </>
  );
};

export default VideoSeekZones;
