import React, { useEffect, useRef, useState } from 'react';
import { MediaItem } from '../types';
import { Preview } from './useHoverPreview';

const CLIP_POINTS = [0.1, 0.3, 0.5, 0.7, 0.9];
const CLIP_MS = 1500;
const GAP_PX = 16;

interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * The popup takes the file's own shape: the largest width and height with the file's aspect
 * ratio that fit the space, so there are no black bars beside a portrait photo or video.
 */
const boxFor = (anchor: DOMRect, isTouch: boolean, aspect: number): Box => {
  const maxWidth = Math.min(window.innerWidth * (isTouch ? 0.92 : 0.5), 720);
  const maxHeight = Math.min(window.innerHeight * (isTouch ? 0.8 : 0.7), 720);
  const width = Math.min(maxWidth, maxHeight * aspect);
  const height = width / aspect;
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

/**
 * Width over height as shown. The thumbnail gives a first guess right away (it is cached from the
 * grid), the file itself corrects it once loaded: the browser applies a photo's EXIF orientation,
 * the thumbnail maker does not.
 */
const useAspect = (item: MediaItem) => {
  const [aspect, setAspect] = useState<number | null>(null);

  useEffect(() => {
    setAspect(null);
    if (!item.thumbnailUrl) {
      setAspect(1);
      return;
    }
    const probe = new Image();
    probe.onload = () => {
      if (probe.naturalWidth && probe.naturalHeight) {
        setAspect((current) => current ?? probe.naturalWidth / probe.naturalHeight);
      }
    };
    probe.onerror = () => setAspect((current) => current ?? 1);
    probe.src = item.thumbnailUrl;
  }, [item.id, item.thumbnailUrl]);

  const fromMedia = (width: number, height: number) => {
    if (width > 0 && height > 0) setAspect(width / height);
  };

  return { aspect, fromMedia };
};

const ClipPlayer: React.FC<{
  item: MediaItem;
  style: React.CSSProperties;
  onSize: (width: number, height: number) => void;
}> = ({ item, style, onSize }) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [clip, setClip] = useState(0);
  const onSizeRef = useRef(onSize);
  onSizeRef.current = onSize;

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
      onSizeRef.current(video.videoWidth, video.videoHeight);
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
        className="block bg-black object-cover"
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
  const { aspect, fromMedia } = useAspect(item);
  // Wait for the shape, a wrongly shaped box for a split second looks worse than a short delay.
  if (aspect === null) return null;

  const isQuarterTurn = item.rotation === 90 || item.rotation === 270;
  const shownAspect = isQuarterTurn ? 1 / aspect : aspect;
  const box = boxFor(anchor, isTouch, shownAspect);
  // The media is laid out unturned, then turned into the box: a quarter turn swaps its sides.
  const media = isQuarterTurn ? { width: box.height, height: box.width } : { width: box.width, height: box.height };

  return (
    <div aria-hidden="true" style={box} className="pointer-events-none fixed z-30 flex items-center justify-center">
      <div
        style={{ ...media, transform: item.rotation ? `rotate(${item.rotation}deg)` : undefined }}
        className="relative shrink-0 overflow-hidden rounded-lg shadow-2xl"
      >
        {item.mediaType === 'video' ? (
          <ClipPlayer key={item.id} item={item} style={media} onSize={fromMedia} />
        ) : (
          // The thumbnail fills the box at once, the full photo draws over it when loaded.
          <img
            src={item.fileUrl}
            alt=""
            onLoad={(event) => fromMedia(event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)}
            style={{
              ...media,
              backgroundImage: item.thumbnailUrl ? `url(${item.thumbnailUrl})` : undefined,
              backgroundSize: 'cover',
            }}
            className="block object-cover"
          />
        )}
      </div>
    </div>
  );
};

export default HoverPreview;
