import { useCallback, useState } from 'react';

export const MIN_SCALE = 1;
export const MAX_SCALE = 5;

export interface Point {
  x: number;
  y: number;
}

export interface ZoomState {
  scale: number;
  offset: Point;
}

export const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

export const useZoom = () => {
  const [zoom, setZoom] = useState<ZoomState>({ scale: 1, offset: { x: 0, y: 0 } });
  const reset = useCallback(() => setZoom({ scale: 1, offset: { x: 0, y: 0 } }), []);
  /** Zoom by a factor around a point given relative to the image center (0,0 = center). */
  const zoomAt = useCallback((factor: number, at: Point = { x: 0, y: 0 }) => {
    setZoom(({ scale, offset }) => {
      const next = clamp(scale * factor, MIN_SCALE, MAX_SCALE);
      if (next === MIN_SCALE) return { scale: 1, offset: { x: 0, y: 0 } };
      const ratio = next / scale;
      // Keep the point under the cursor in place while the scale changes.
      return {
        scale: next,
        offset: { x: at.x - (at.x - offset.x) * ratio, y: at.y - (at.y - offset.y) * ratio },
      };
    });
  }, []);
  return { zoom, setZoom, reset, zoomAt };
};
