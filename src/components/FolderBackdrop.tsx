import React, { useEffect, useMemo, useRef } from 'react';

// Slow on purpose: the strip is a hint of what is inside, not something to watch.
const MIN_PAN_PIXELS_PER_SECOND = 10;
const MAX_PAN_PIXELS_PER_SECOND = 26;
const MIN_PHOTO_WIDTH = 104;
const MAX_PHOTO_WIDTH = 248;
const EASINGS = ['ease-in-out', 'linear', 'ease', 'cubic-bezier(0.45, 0, 0.2, 1)'];

const between = (low: number, high: number) => low + Math.random() * (high - low);

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Darkened strip of the folder's own photos behind its row, panning back and forth.
 * Each photo appears once, so the strip pans to its end and back instead of looping.
 */
const FolderBackdrop: React.FC<{ urls: string[] }> = ({ urls }) => {
  const frameRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  // Rolled once per row: every row gets its own photo widths, speed, direction and starting
  // point, so a page of folders never moves in step like one block.
  const motion = useMemo(
    () => ({
      widths: urls.map(() => Math.round(between(MIN_PHOTO_WIDTH, MAX_PHOTO_WIDTH))),
      speed: between(MIN_PAN_PIXELS_PER_SECOND, MAX_PAN_PIXELS_PER_SECOND),
      reverse: Math.random() < 0.5,
      startAt: Math.random(),
      easing: EASINGS[Math.floor(Math.random() * EASINGS.length)],
    }),
    [urls],
  );

  useEffect(() => {
    const frame = frameRef.current;
    const strip = stripRef.current;
    if (!frame || !strip || prefersReducedMotion()) return;
    let animation: Animation | null = null;

    // Distance depends on the row width and on how many photos loaded, so measure it.
    const restart = () => {
      animation?.cancel();
      const distance = strip.scrollWidth - frame.clientWidth;
      if (distance <= 0) return;
      const ends = [{ transform: 'translateX(0)' }, { transform: `translateX(-${distance}px)` }];
      const duration = (distance / motion.speed) * 1000;
      animation = strip.animate(motion.reverse ? ends.reverse() : ends, {
        duration,
        direction: 'alternate',
        iterations: Infinity,
        easing: motion.easing,
      });
      animation.currentTime = motion.startAt * duration * 2;
    };

    const observer = new ResizeObserver(restart);
    observer.observe(frame);
    observer.observe(strip);
    return () => {
      observer.disconnect();
      animation?.cancel();
    };
  }, [urls, motion]);

  return (
    <div ref={frameRef} aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div ref={stripRef} className="flex h-full w-max">
        {urls.map((url, index) => (
          <img
            key={url}
            src={url}
            alt=""
            loading="lazy"
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
            style={{ width: motion.widths[index] }}
            className="h-full shrink-0 object-cover"
          />
        ))}
      </div>
      {/* Darkest where the folder name sits, so white text keeps its contrast over any photo. */}
      <div className="absolute inset-0 bg-gradient-to-r from-gray-950/85 via-gray-950/65 to-gray-950/40" />
    </div>
  );
};

export default FolderBackdrop;
