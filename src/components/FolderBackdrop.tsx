import React, { useEffect, useRef } from 'react';

// Slow on purpose: the strip is a hint of what is inside, not something to watch.
const PAN_PIXELS_PER_SECOND = 18;

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Darkened strip of the folder's own photos behind its row, panning back and forth.
 * Each photo appears once, so the strip pans to its end and back instead of looping.
 */
const FolderBackdrop: React.FC<{ urls: string[] }> = ({ urls }) => {
  const frameRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);

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
      animation = strip.animate(
        [{ transform: 'translateX(0)' }, { transform: `translateX(-${distance}px)` }],
        {
          duration: (distance / PAN_PIXELS_PER_SECOND) * 1000,
          direction: 'alternate',
          iterations: Infinity,
          easing: 'ease-in-out',
        },
      );
    };

    const observer = new ResizeObserver(restart);
    observer.observe(frame);
    observer.observe(strip);
    return () => {
      observer.disconnect();
      animation?.cancel();
    };
  }, [urls]);

  return (
    <div ref={frameRef} aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div ref={stripRef} className="flex h-full w-max">
        {urls.map((url) => (
          <img
            key={url}
            src={url}
            alt=""
            loading="lazy"
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
            className="h-full w-44 shrink-0 object-cover"
          />
        ))}
      </div>
      {/* Darkest where the folder name sits, so white text keeps its contrast over any photo. */}
      <div className="absolute inset-0 bg-gradient-to-r from-gray-950/85 via-gray-950/65 to-gray-950/40" />
    </div>
  );
};

export default FolderBackdrop;
