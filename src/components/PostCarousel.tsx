import React, { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Play } from 'lucide-react';
import { MediaSlide, Platform, Post } from '../types';
import { fetchFreshMedia } from '../utils/api';
import { focusRing } from './ui';

interface PostCarouselProps {
  post: Post;
  platform: Platform;
  username: string;
  isSaved: boolean;
}

/** Raw CDN URL back out of our /api/media?url= proxy link. */
const rawUrl = (proxied: string): string => {
  try {
    return new URL(proxied, window.location.origin).searchParams.get('url') ?? proxied;
  } catch {
    return proxied;
  }
};

const AUTOPLAY_MS = 4000;

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const SlideView: React.FC<{
  slide: MediaSlide;
  alt: string;
  isActive: boolean;
  onPlayingChange: (playing: boolean) => void;
  // Fresh link for this slide when the cached one expired, null if none could be had.
  onExpired: () => Promise<string | null>;
}> = ({ slide, alt, isActive, onPlayingChange, onExpired }) => {
  const [failed, setFailed] = useState(false);
  const [videoUrl, setVideoUrl] = useState(slide.url);
  const [isRenewing, setIsRenewing] = useState(false);
  const hasRenewed = useRef(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  // Cached pages live for a month, video links for hours to days. Ask once for a new link,
  // only when this video is actually played, since that costs a platform request.
  const handleVideoError = () => {
    if (hasRenewed.current) {
      setFailed(true);
      return;
    }
    hasRenewed.current = true;
    setIsRenewing(true);
    onExpired()
      .then((fresh) => {
        if (fresh) {
          setVideoUrl(fresh);
        } else {
          setFailed(true);
        }
      })
      .catch((error: unknown) => {
        console.error('fetchFreshMedia failed', error);
        setFailed(true);
      })
      .finally(() => setIsRenewing(false));
  };

  // Leaving a slide should stop its video, otherwise audio keeps playing off screen.
  useEffect(() => {
    if (!isActive) videoRef.current?.pause();
  }, [isActive]);

  if (failed || !slide.url) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-gray-200 px-4 text-center text-sm text-gray-700">
        {slide.url ? 'Media gagal dimuat. Klik "Scrape ulang" untuk link baru.' : 'Tidak ada media'}
      </div>
    );
  }
  if (isRenewing) {
    return (
      <div role="status" className="flex h-full w-full items-center justify-center bg-black px-4 text-center text-sm text-white">
        Link video kedaluwarsa, mengambil link baru...
      </div>
    );
  }
  if (slide.type === 'video') {
    return (
      <video
        ref={videoRef}
        src={isActive ? videoUrl : undefined}
        poster={slide.posterUrl ?? undefined}
        controls
        playsInline
        preload="none"
        onPlay={() => onPlayingChange(true)}
        onPause={() => onPlayingChange(false)}
        onEnded={() => onPlayingChange(false)}
        onError={handleVideoError}
        className="h-full w-full bg-black object-contain"
      />
    );
  }
  return (
    <img
      src={slide.url}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover"
    />
  );
};

/** Post media with arrows, dots and autoplay that pauses on hover, focus, video play and off screen. */
const PostCarousel: React.FC<PostCarouselProps> = ({ post, platform, username, isSaved }) => {
  const slides: MediaSlide[] = post.media.length
    ? post.media
    : [{ type: 'photo', url: post.imageUrl, posterUrl: null }];
  const total = slides.length;
  const [index, setIndex] = useState(0);
  const [isHovered, setIsHovered] = useState(false);
  const [hasFocus, setHasFocus] = useState(false);
  const [isVideoPlaying, setIsVideoPlaying] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = rootRef.current;
    if (!element || total < 2) return;
    const observer = new IntersectionObserver(([entry]) => setIsVisible(entry.isIntersecting), {
      threshold: 0.6,
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [total]);

  const isPaused = isHovered || hasFocus || isVideoPlaying || !isVisible || prefersReducedMotion();

  useEffect(() => {
    if (total < 2 || isPaused) return;
    const timer = window.setInterval(() => setIndex((current) => (current + 1) % total), AUTOPLAY_MS);
    return () => window.clearInterval(timer);
  }, [total, isPaused]);

  const go = (next: number) => {
    setIsVideoPlaying(false);
    setIndex((next + total) % total);
  };

  // Frosted glass so the arrows do not cover the photo. Dark tint + white border keeps the
  // white icon readable on both bright and dark images.
  const arrowClass = `absolute top-1/2 -translate-y-1/2 flex h-11 w-11 items-center justify-center rounded-full border border-white/40 bg-black/20 text-white backdrop-blur-md transition-colors hover:bg-black/35 ${focusRing}`;

  return (
    <div
      ref={rootRef}
      role="group"
      aria-roledescription="carousel"
      aria-label={total > 1 ? `Post dengan ${total} slide` : 'Media post'}
      className={`relative h-80 w-full overflow-hidden bg-gray-200 ${isSaved ? 'grayscale opacity-60' : ''}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocus={() => setHasFocus(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHasFocus(false);
      }}
    >
      <div
        className="flex h-full transition-transform duration-500 ease-out motion-reduce:transition-none"
        style={{ transform: `translateX(-${index * 100}%)` }}
      >
        {slides.map((slide, slideIndex) => (
          <div
            key={`${slide.url}-${slideIndex}`}
            className="relative h-full w-full shrink-0"
            role="group"
            aria-roledescription="slide"
            aria-label={`${slideIndex + 1} dari ${total}`}
            aria-hidden={slideIndex !== index}
          >
            <SlideView
              slide={slide}
              alt={post.caption || 'Post tanpa caption'}
              isActive={slideIndex === index}
              onPlayingChange={setIsVideoPlaying}
              onExpired={async () => {
                const fresh = await fetchFreshMedia(platform, username, post.id, rawUrl(slide.url));
                return fresh.urls[slideIndex] ?? null;
              }}
            />
            {slide.type === 'video' && slideIndex !== index && (
              <Play className="absolute bottom-3 right-3 h-6 w-6 text-white drop-shadow" aria-hidden="true" />
            )}
          </div>
        ))}
      </div>

      {total > 1 && (
        <>
          <button type="button" onClick={() => go(index - 1)} aria-label="Slide sebelumnya" className={`left-2 ${arrowClass}`}>
            <ChevronLeft className="h-6 w-6 drop-shadow" aria-hidden="true" />
          </button>
          <button type="button" onClick={() => go(index + 1)} aria-label="Slide berikutnya" className={`right-2 ${arrowClass}`}>
            <ChevronRight className="h-6 w-6 drop-shadow" aria-hidden="true" />
          </button>
          <div className="absolute bottom-2 left-0 right-0 flex justify-center gap-1">
            {slides.map((slide, slideIndex) => (
              <button
                key={`dot-${slide.url}-${slideIndex}`}
                type="button"
                onClick={() => go(slideIndex)}
                aria-label={`Ke slide ${slideIndex + 1}`}
                aria-current={slideIndex === index}
                className={`flex h-6 w-6 items-center justify-center rounded-full ${focusRing}`}
              >
                <span
                  className={`block h-2 w-2 rounded-full ${slideIndex === index ? 'bg-white' : 'bg-white/50'} shadow`}
                />
              </button>
            ))}
          </div>
          <span className="absolute top-2 right-2 rounded bg-black/70 px-2 py-0.5 text-xs font-semibold text-white">
            {index + 1}/{total}
          </span>
        </>
      )}
    </div>
  );
};

export default PostCarousel;
