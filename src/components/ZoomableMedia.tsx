import React, { useEffect, useRef, useState } from 'react';
import { Rotation } from '../types';
import VideoControls from './VideoControls';
import VideoSeekZones, { SeekFlash } from './VideoSeekZones';
import { MAX_SCALE, MIN_SCALE, Point, ZoomState, clamp } from './zoom';

const WHEEL_STEP = 0.0015;
const DOUBLE_TAP_SCALE = 2.5;

// Pointer travel below this still counts as a tap, not a pan.
const TAP_SLOP_PX = 6;

interface ZoomableMediaProps {
  kind: 'photo' | 'video';
  src: string;
  alt: string;
  poster?: string;
  videoRef?: React.RefObject<HTMLVideoElement>;
  zoom: ZoomState;
  setZoom: React.Dispatch<React.SetStateAction<ZoomState>>;
  zoomAt: (factor: number, at?: Point) => void;
  // Video only: tap zones for seeking, play and pause.
  onSeek?: (seconds: number) => void;
  onTogglePlay?: () => void;
  onToggleFullscreen?: () => void;
  seekFlash?: SeekFlash | null;
  loop?: boolean;
  autoPlay?: boolean;
  onEnded?: () => void;
  rotation?: Rotation;
  isFullscreen?: boolean;
}

/**
 * Wheel, drag to pan and two finger pinch for photos and videos, double click for photos.
 * Videos get our own control bar, laid over the frame: it stays upright and in place while the
 * video is zoomed or rotated. A tap on the video itself plays or pauses.
 */
const ZoomableMedia: React.FC<ZoomableMediaProps> = ({
  kind,
  src,
  alt,
  poster,
  videoRef,
  loop = false,
  autoPlay = false,
  onEnded,
  rotation = 0,
  isFullscreen = false,
  zoom,
  setZoom,
  zoomAt,
  onSeek,
  onTogglePlay,
  onToggleFullscreen,
  seekFlash = null,
}) => {
  const frameRef = useRef<HTMLDivElement>(null);
  const tapStart = useRef<Point | null>(null);
  const pointers = useRef(new Map<number, Point>());
  const pinchStart = useRef<{ distance: number; scale: number } | null>(null);
  const lastPan = useRef<Point | null>(null);
  const [frameSize, setFrameSize] = useState<{ width: number; height: number } | null>(null);

  // A quarter turn swaps width and height, so the size limits come from the frame in pixels.
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) =>
      setFrameSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const fromCenter = (clientX: number, clientY: number): Point => {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: clientX - rect.left - rect.width / 2, y: clientY - rect.top - rect.height / 2 };
  };

  // Native listener: React's wheel handler is passive, so it could not stop the page scroll.
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = frame.getBoundingClientRect();
      zoomAt(Math.exp(-event.deltaY * WHEEL_STEP), {
        x: event.clientX - rect.left - rect.width / 2,
        y: event.clientY - rect.top - rect.height / 2,
      });
    };
    frame.addEventListener('wheel', handleWheel, { passive: false });
    return () => frame.removeEventListener('wheel', handleWheel);
  }, [zoomAt]);

  const handleDoubleClick = (event: React.MouseEvent) => {
    // On a video, double click is too close to the player's own clicks.
    if (kind === 'video') return;
    if (zoom.scale > 1) {
      setZoom({ scale: 1, offset: { x: 0, y: 0 } });
    } else {
      zoomAt(DOUBLE_TAP_SCALE, fromCenter(event.clientX, event.clientY));
    }
  };

  const handlePointerDown = (event: React.PointerEvent) => {
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    // An unzoomed video keeps the pointer, so its seek bar and buttons still work.
    // Capture only once there is something for us to do: a pinch, or a pan when zoomed.
    const ownsGesture = kind === 'photo' || zoom.scale > 1 || pointers.current.size === 2;
    if (ownsGesture) {
      pointers.current.forEach((_, id) => {
        if (event.currentTarget.hasPointerCapture(id)) return;
        try {
          event.currentTarget.setPointerCapture(id);
        } catch {
          // The other finger may already be gone.
        }
      });
    }
    if (pointers.current.size === 2) {
      const [a, b] = Array.from(pointers.current.values());
      pinchStart.current = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: zoom.scale };
      lastPan.current = null;
      tapStart.current = null;
    } else {
      lastPan.current = { x: event.clientX, y: event.clientY };
      tapStart.current = { x: event.clientX, y: event.clientY };
    }
  };

  const handlePointerMove = (event: React.PointerEvent) => {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (pointers.current.size === 2 && pinchStart.current) {
      const [a, b] = Array.from(pointers.current.values());
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      const target = clamp(
        (pinchStart.current.scale * distance) / pinchStart.current.distance,
        MIN_SCALE,
        MAX_SCALE,
      );
      zoomAt(target / zoom.scale, fromCenter((a.x + b.x) / 2, (a.y + b.y) / 2));
      return;
    }
    if (lastPan.current && zoom.scale > 1) {
      const dx = event.clientX - lastPan.current.x;
      const dy = event.clientY - lastPan.current.y;
      lastPan.current = { x: event.clientX, y: event.clientY };
      setZoom((current) => ({
        ...current,
        offset: { x: current.offset.x + dx, y: current.offset.y + dy },
      }));
    }
  };

  const handlePointerUp = (event: React.PointerEvent) => {
    const start = tapStart.current;
    const isTap =
      start !== null &&
      Math.hypot(event.clientX - start.x, event.clientY - start.y) < TAP_SLOP_PX &&
      pointers.current.size === 1;
    const target = event.target as HTMLElement;
    const onZone = target.closest('[data-seek-zone]') !== null;
    const onControls = target.closest('[data-video-controls]') !== null;
    if (isTap && kind === 'video' && !onZone && !onControls) {
      const video = videoRef?.current;
      if (video) void (video.paused ? video.play() : video.pause());
    }
    tapStart.current = null;
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinchStart.current = null;
    lastPan.current = pointers.current.size === 1 ? Array.from(pointers.current.values())[0] : null;
  };

  const isZoomed = zoom.scale > 1;
  const isQuarterTurn = rotation === 90 || rotation === 270;
  const mediaStyle: React.CSSProperties = {
    transform: `translate(${zoom.offset.x}px, ${zoom.offset.y}px) scale(${zoom.scale}) rotate(${rotation}deg)`,
    ...(isQuarterTurn && frameSize ? { maxWidth: frameSize.height, maxHeight: frameSize.width } : {}),
  };
  const mediaClass = `${
    isQuarterTurn ? '' : 'max-h-full max-w-full'
  } object-contain transition-transform duration-75 ease-out motion-reduce:transition-none`;

  return (
    <div
      ref={frameRef}
      onDoubleClick={handleDoubleClick}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      // touch-none: the browser would otherwise scroll or zoom the page on pinch.
      className={`relative flex h-full w-full touch-none select-none items-center justify-center overflow-hidden ${
        isZoomed ? 'cursor-grab active:cursor-grabbing' : kind === 'photo' ? 'cursor-zoom-in' : ''
      }`}
    >
      {kind === 'video' ? (
        <video
          ref={videoRef}
          src={src}
          poster={poster}
          controls={false}
          loop={loop}
          autoPlay={autoPlay}
          onEnded={onEnded}
          playsInline
          aria-label={alt}
          style={mediaStyle}
          className={mediaClass}
        />
      ) : (
        <img src={src} alt={alt} draggable={false} style={mediaStyle} className={mediaClass} />
      )}
      {kind === 'video' && onSeek && onTogglePlay && onToggleFullscreen && (
        <VideoSeekZones
          onSeek={onSeek}
          onTogglePlay={onTogglePlay}
          onToggleFullscreen={onToggleFullscreen}
          flash={seekFlash}
        />
      )}
      {kind === 'video' && videoRef && onToggleFullscreen && (
        <VideoControls
          videoRef={videoRef}
          src={src}
          rotation={rotation}
          isFullscreen={isFullscreen}
          onToggleFullscreen={onToggleFullscreen}
        />
      )}
    </div>
  );
};

export default ZoomableMedia;
