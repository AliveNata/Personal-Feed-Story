import React, { useEffect, useRef, useState } from 'react';
import { MediaItem } from '../types';
import { Preview } from './useHoverPreview';

const CLIP_POINTS = [0.1, 0.3, 0.5, 0.7, 0.9];
const CLIP_MS = 1500;
const GAP_PX = 16;

const boxFor = (anchor: DOMRect, isTouch: boolean) => {
  const width = Math.min(window.innerWidth * (isTouch ? 0.92 : 0.5), 720);
  const height = Math.min(window.innerHeight * 0.7, 720);
  if (isTouch) {
    return { left: (window.innerWidth - width) / 2, top: (window.innerHeight - height) / 2, width, height };
  }
  // Beside the tile, on whichever side has more room, so the cursor never ends up over it.
  const roomRight = window.innerWidth - anchor.right;
  const left =
    roomRight >= anchor.left
      ? Math.min(anchor.right + GAP_PX, window.innerWidth - width - GAP_PX)
      : Math.max(anchor.left - GAP_PX - width, GAP_PX);
  const centered = anchor.top + anchor.height / 2 - height / 2;
  const top = Math.min(Math.max(centered, GAP_PX), window.innerHeight - height - GAP_PX);
  return { left, top, width, height };
};

const ClipPlayer: React.FC<{ item: MediaItem; style: React.CSSProperties }> = ({ item, style }) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [clip, setClip] = useState(0);

  // Jump through the video: a short look at five points instead of the first seconds only.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let index = 0;
    let interval: number | null = null;
    const jump = () => {
      if (!Number.isFinite(video.duration) || video.duration <= 0) return;
      video.currentTime = video.duration * CLIP_POINTS[index];
      setClip(index);
      index = (index + 1) % CLIP_POINTS.length;
    };
    const start = () => {
      jump();
      video.play().catch((error: unknown) => console.warn('preview autoplay blocked', error));
      interval = window.setInterval(jump, CLIP_MS);
    };
    video.addEventListener('loadedmetadata', start, { once: true });
    return () => {
      video.removeEventListener('loadedmetadata', start);
      if (interval !== null) window.clearInterval(interval);
      video.pause();
    };
  }, [item.id]);

  return (
    <>
      <video
        ref={videoRef}
        src={item.fileUrl}
        poster={item.thumbnailUrl ?? undefined}
        muted
        playsInline
        preload="metadata"
        style={style}
        className="object-contain"
      />
      <span className="absolute bottom-2 left-2 rounded bg-black/70 px-2 py-0.5 text-xs text-white">
        Cuplikan {clip + 1}/{CLIP_POINTS.length}
      </span>
    </>
  );
};

/** The popup itself. Decorative and out of the pointer's way: the viewer stays the real way in. */
const HoverPreview: React.FC<{ preview: Preview }> = ({ preview }) => {
  const { item, anchor, isTouch } = preview;
  const box = boxFor(anchor, isTouch);
  const isQuarterTurn = item.rotation === 90 || item.rotation === 270;
  const mediaStyle: React.CSSProperties = {
    maxWidth: isQuarterTurn ? box.height : box.width,
    maxHeight: isQuarterTurn ? box.width : box.height,
    transform: item.rotation ? `rotate(${item.rotation}deg)` : undefined,
  };

  return (
    <div
      aria-hidden="true"
      style={box}
      className="pointer-events-none fixed z-30 flex items-center justify-center overflow-hidden rounded-lg bg-black/90 shadow-2xl"
    >
      {item.mediaType === 'video' ? (
        <ClipPlayer key={item.id} item={item} style={mediaStyle} />
      ) : (
        // The thumbnail shows at once, the full photo replaces it when loaded.
        <img
          src={item.fileUrl}
          alt=""
          style={{
            ...mediaStyle,
            backgroundImage: item.thumbnailUrl ? `url(${item.thumbnailUrl})` : undefined,
            backgroundSize: 'contain',
            backgroundPosition: 'center',
            backgroundRepeat: 'no-repeat',
          }}
          className="object-contain"
        />
      )}
    </div>
  );
};

export default HoverPreview;
