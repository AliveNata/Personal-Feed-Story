import React, { useEffect, useRef } from 'react';
import { MAX_SCALE, MIN_SCALE, Point, ZoomState, clamp } from './zoom';

const WHEEL_STEP = 0.0015;
const DOUBLE_TAP_SCALE = 2.5;

interface ZoomableImageProps {
  src: string;
  alt: string;
  zoom: ZoomState;
  setZoom: React.Dispatch<React.SetStateAction<ZoomState>>;
  zoomAt: (factor: number, at?: Point) => void;
}

/** Wheel, double click, drag to pan, and two finger pinch on touch screens. */
const ZoomableImage: React.FC<ZoomableImageProps> = ({ src, alt, zoom, setZoom, zoomAt }) => {
  const frameRef = useRef<HTMLDivElement>(null);
  const pointers = useRef(new Map<number, Point>());
  const pinchStart = useRef<{ distance: number; scale: number } | null>(null);
  const lastPan = useRef<Point | null>(null);

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
    if (zoom.scale > 1) {
      setZoom({ scale: 1, offset: { x: 0, y: 0 } });
    } else {
      zoomAt(DOUBLE_TAP_SCALE, fromCenter(event.clientX, event.clientY));
    }
  };

  const handlePointerDown = (event: React.PointerEvent) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = Array.from(pointers.current.values());
      pinchStart.current = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: zoom.scale };
      lastPan.current = null;
    } else {
      lastPan.current = { x: event.clientX, y: event.clientY };
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
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinchStart.current = null;
    lastPan.current = pointers.current.size === 1 ? Array.from(pointers.current.values())[0] : null;
  };

  const isZoomed = zoom.scale > 1;

  return (
    <div
      ref={frameRef}
      onDoubleClick={handleDoubleClick}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      // touch-none: the browser would otherwise scroll or zoom the page on pinch.
      className={`flex h-full w-full touch-none select-none items-center justify-center overflow-hidden ${
        isZoomed ? 'cursor-grab active:cursor-grabbing' : 'cursor-zoom-in'
      }`}
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        style={{
          transform: `translate(${zoom.offset.x}px, ${zoom.offset.y}px) scale(${zoom.scale})`,
        }}
        className="max-h-full max-w-full object-contain transition-transform duration-75 ease-out motion-reduce:transition-none"
      />
    </div>
  );
};

export default ZoomableImage;
