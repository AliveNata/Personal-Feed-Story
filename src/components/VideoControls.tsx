import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Captions,
  Check,
  Loader2,
  Maximize,
  Minimize,
  Pause,
  Play,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { Rotation } from '../types';
import { SubtitleControls } from './useSubtitles';

// The preview is drawn this wide; its height follows the video.
const PREVIEW_WIDTH = 176;
// While playing, the bar fades after this long without pointer movement, like most players.
const HIDE_AFTER_MS = 2500;

const formatTime = (seconds: number): string => {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = String(whole % 60).padStart(2, '0');
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${secs}` : `${minutes}:${secs}`;
};

/**
 * Frames for the seek bar preview, read from a second, hidden copy of the same video: seeking it
 * leaves the one being watched alone. Requests that arrive while a seek runs collapse into the
 * latest one, so a fast sweep across the bar does not queue dozens of seeks on a 1 GB file.
 */
const useFramePreview = (src: string) => {
  const probeRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const busy = useRef(false);
  const pending = useRef<number | null>(null);
  const [ready, setReady] = useState(false);
  const [aspect, setAspect] = useState(16 / 9);

  useEffect(() => {
    const probe = document.createElement('video');
    probe.muted = true;
    probe.preload = 'auto';
    probe.playsInline = true;
    probe.src = src;
    probeRef.current = probe;
    setReady(false);

    const draw = () => {
      const canvas = canvasRef.current;
      if (canvas && probe.videoWidth > 0) {
        canvas.width = PREVIEW_WIDTH;
        canvas.height = Math.round(PREVIEW_WIDTH / (probe.videoWidth / probe.videoHeight));
        canvas.getContext('2d')?.drawImage(probe, 0, 0, canvas.width, canvas.height);
        setReady(true);
      }
      busy.current = false;
      const next = pending.current;
      pending.current = null;
      if (next !== null) {
        busy.current = true;
        probe.currentTime = next;
      }
    };
    const onMetadata = () => {
      if (probe.videoWidth > 0) setAspect(probe.videoWidth / probe.videoHeight);
    };
    probe.addEventListener('seeked', draw);
    probe.addEventListener('loadedmetadata', onMetadata);
    return () => {
      probe.removeEventListener('seeked', draw);
      probe.removeEventListener('loadedmetadata', onMetadata);
      probe.removeAttribute('src');
      probe.load();
      probeRef.current = null;
    };
  }, [src]);

  const requestFrame = useCallback((time: number) => {
    const probe = probeRef.current;
    if (!probe || probe.readyState < HTMLMediaElement.HAVE_METADATA) return;
    if (busy.current) {
      pending.current = time;
      return;
    }
    busy.current = true;
    probe.currentTime = time;
  }, []);

  return { canvasRef, requestFrame, ready, aspect };
};

interface VideoControlsProps {
  videoRef: React.RefObject<HTMLVideoElement>;
  src: string;
  rotation: Rotation;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  // Null until the video has subtitles: the CC button appears only then.
  subtitles?: SubtitleControls | null;
}

/**
 * Play, time, a seek bar that previews the frame under the pointer, volume and full screen.
 * Built instead of the browser's own controls: those cannot show a preview, turn with a rotated
 * video, and their full screen button does not work from inside the viewer dialog.
 */
const VideoControls: React.FC<VideoControlsProps> = ({
  videoRef,
  src,
  rotation,
  isFullscreen,
  onToggleFullscreen,
  subtitles,
}) => {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // A click anywhere else closes the subtitle menu, and Escape closes the menu, not the viewer.
  useEffect(() => {
    if (!isMenuOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setIsMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setIsMenuOpen(false);
    };
    // A closing dialog would also take the menu with it, so it must not see this Escape.
    const keepDialogOpen = (event: Event) => event.preventDefault();
    const dialog = menuRef.current?.closest('dialog');
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape, true);
    dialog?.addEventListener('cancel', keepDialogOpen);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape, true);
      dialog?.removeEventListener('cancel', keepDialogOpen);
    };
  }, [isMenuOpen]);
  const [isPlaying, setIsPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [hover, setHover] = useState<{ time: number; x: number } | null>(null);
  const [isVisible, setIsVisible] = useState(true);
  const barRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const isScrubbing = useRef(false);
  const hideTimer = useRef<number | null>(null);
  const { canvasRef, requestFrame, ready, aspect } = useFramePreview(src);

  // Follow the video, whatever changed it: these buttons, the keyboard, the tap zones.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const sync = () => {
      setIsPlaying(!video.paused);
      setCurrent(video.currentTime);
      setDuration(Number.isFinite(video.duration) ? video.duration : 0);
      setIsMuted(video.muted);
      setVolume(video.volume);
      const ranges = video.buffered;
      setBuffered(ranges.length ? ranges.end(ranges.length - 1) : 0);
    };
    const events = ['play', 'pause', 'timeupdate', 'durationchange', 'loadedmetadata', 'volumechange', 'progress', 'seeked'];
    events.forEach((name) => video.addEventListener(name, sync));
    sync();
    return () => events.forEach((name) => video.removeEventListener(name, sync));
  }, [videoRef, src]);

  const showForAWhile = useCallback(() => {
    setIsVisible(true);
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      // Stays while paused, scrubbing, or while a control has keyboard focus.
      const video = videoRef.current;
      const hasFocus = barRef.current?.contains(document.activeElement) ?? false;
      if (video && !video.paused && !isScrubbing.current && !hasFocus) {
        setIsVisible(false);
        setIsMenuOpen(false);
      }
    }, HIDE_AFTER_MS);
  }, [videoRef]);

  useEffect(() => {
    const frame = barRef.current?.parentElement;
    if (!frame) return;
    frame.addEventListener('pointermove', showForAWhile);
    frame.addEventListener('pointerdown', showForAWhile);
    return () => {
      frame.removeEventListener('pointermove', showForAWhile);
      frame.removeEventListener('pointerdown', showForAWhile);
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    };
  }, [showForAWhile]);

  useEffect(() => {
    if (!isPlaying) setIsVisible(true);
    else showForAWhile();
  }, [isPlaying, showForAWhile]);

  const timeAt = (clientX: number) => {
    const track = trackRef.current;
    if (!track || duration <= 0) return null;
    const rect = track.getBoundingClientRect();
    const ratio = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
    return { time: ratio * duration, x: ratio * rect.width };
  };

  const preview = (clientX: number) => {
    const point = timeAt(clientX);
    if (!point) return;
    setHover(point);
    requestFrame(point.time);
  };

  const seekTo = (clientX: number) => {
    const point = timeAt(clientX);
    const video = videoRef.current;
    if (point && video) video.currentTime = point.time;
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      video.play().catch((error: unknown) => console.warn('video play refused', error));
    } else {
      video.pause();
    }
  };

  const toggleMute = () => {
    const video = videoRef.current;
    if (video) video.muted = !video.muted;
  };

  const onSliderKey = (event: React.KeyboardEvent) => {
    const video = videoRef.current;
    if (!video) return;
    const steps: Record<string, number> = { ArrowLeft: -5, ArrowRight: 5, PageDown: -60, PageUp: 60 };
    if (event.key in steps) {
      event.preventDefault();
      event.stopPropagation();
      video.currentTime = Math.min(Math.max(video.currentTime + steps[event.key], 0), duration);
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      event.stopPropagation();
      video.currentTime = event.key === 'Home' ? 0 : duration;
    }
  };

  const played = duration > 0 ? (current / duration) * 100 : 0;
  const loaded = duration > 0 ? (buffered / duration) * 100 : 0;
  const isQuarterTurn = rotation === 90 || rotation === 270;
  const previewAspect = isQuarterTurn ? 1 / aspect : aspect;
  const previewWidth = isQuarterTurn ? Math.round(PREVIEW_WIDTH / aspect) : PREVIEW_WIDTH;
  const trackWidth = trackRef.current?.clientWidth ?? 0;
  const previewLeft = hover
    ? Math.min(Math.max(hover.x - previewWidth / 2, 0), Math.max(trackWidth - previewWidth, 0))
    : 0;

  const cue = subtitles?.lines?.find((line) => line.start <= current && current < line.end) ?? null;
  const isSubtitleOn = Boolean(subtitles && subtitles.choice !== 'off');

  const button = 'flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-white hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white';

  return (
    <>
      {cue && (
        // Above the bar while it shows, lower once it fades, like most players.
        <div
          aria-live="off"
          className={`pointer-events-none absolute inset-x-0 z-10 flex justify-center px-4 transition-[bottom] duration-200 motion-reduce:transition-none ${
            isVisible ? 'bottom-28' : 'bottom-6'
          }`}
        >
          <p className="max-w-[92%] whitespace-pre-line rounded bg-black/75 px-2 py-0.5 text-center text-base leading-snug text-white sm:text-lg">
            {cue.text}
          </p>
        </div>
      )}
      <div
        ref={barRef}
        data-video-controls
        // The frame pans and zooms on pointer events: none of that may start from the controls.
        onPointerDown={(event) => event.stopPropagation()}
        onPointerUp={(event) => event.stopPropagation()}
        onDoubleClick={(event) => event.stopPropagation()}
        onFocus={() => setIsVisible(true)}
        className={`absolute inset-x-0 bottom-0 z-20 bg-gradient-to-t from-black/80 to-transparent px-3 pb-2 pt-8 transition-opacity duration-200 motion-reduce:transition-none ${
          isVisible ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
      >
        <div
          className="relative"
          onPointerMove={(event) => {
            if (event.pointerType === 'mouse' || isScrubbing.current) preview(event.clientX);
            if (isScrubbing.current) seekTo(event.clientX);
          }}
          onPointerLeave={() => {
            if (!isScrubbing.current) setHover(null);
          }}
        >
          {hover && (
            <div
              aria-hidden="true"
              style={{ left: previewLeft, width: previewWidth }}
              className="pointer-events-none absolute bottom-6 overflow-hidden rounded-md border-2 border-white bg-black shadow-lg"
            >
              <div
                className="relative flex items-center justify-center overflow-hidden"
                style={{ width: previewWidth, height: Math.round(previewWidth / previewAspect) }}
              >
                <canvas
                  ref={canvasRef}
                  style={{ transform: rotation ? `rotate(${rotation}deg)` : undefined, width: PREVIEW_WIDTH }}
                  className={`shrink-0 ${ready ? '' : 'opacity-0'}`}
                />
              </div>
              <span className="absolute inset-x-0 bottom-0 bg-black/70 py-0.5 text-center text-xs font-semibold text-white">
                {formatTime(hover.time)}
              </span>
            </div>
          )}
          <div
            ref={trackRef}
            role="slider"
            tabIndex={0}
            aria-label="Posisi video"
            aria-valuemin={0}
            aria-valuemax={Math.round(duration)}
            aria-valuenow={Math.round(current)}
            aria-valuetext={`${formatTime(current)} dari ${formatTime(duration)}`}
            onKeyDown={onSliderKey}
            onPointerDown={(event) => {
              isScrubbing.current = true;
              event.currentTarget.setPointerCapture(event.pointerId);
              preview(event.clientX);
              seekTo(event.clientX);
            }}
            onPointerUp={(event) => {
              isScrubbing.current = false;
              event.currentTarget.releasePointerCapture(event.pointerId);
              if (event.pointerType !== 'mouse') setHover(null);
            }}
            onPointerCancel={() => {
              isScrubbing.current = false;
              setHover(null);
            }}
            // Tall hit area for fingers, thin line for the eye.
            className="group relative flex h-6 cursor-pointer touch-none items-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
          >
            <div className="relative h-1 w-full rounded-full bg-white/30 transition-[height] group-hover:h-1.5">
              <div className="absolute inset-y-0 left-0 rounded-full bg-white/40" style={{ width: `${loaded}%` }} />
              <div className="absolute inset-y-0 left-0 rounded-full bg-red-600" style={{ width: `${played}%` }} />
              <div
                className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-red-600 shadow"
                style={{ left: `${played}%` }}
              />
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1 text-white">
          <button type="button" onClick={togglePlay} aria-label={isPlaying ? 'Jeda' : 'Putar'} className={button}>
            {isPlaying ? <Pause className="h-5 w-5" aria-hidden="true" /> : <Play className="h-5 w-5" aria-hidden="true" />}
          </button>
          <button
            type="button"
            onClick={toggleMute}
            aria-label={isMuted ? 'Nyalakan suara' : 'Bisukan'}
            className={button}
          >
            {isMuted || volume === 0 ? (
              <VolumeX className="h-5 w-5" aria-hidden="true" />
            ) : (
              <Volume2 className="h-5 w-5" aria-hidden="true" />
            )}
          </button>
          <label className="sr-only" htmlFor="video-volume">
            Volume
          </label>
          <input
            id="video-volume"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={isMuted ? 0 : volume}
            onChange={(event) => {
              const video = videoRef.current;
              if (!video) return;
              video.volume = Number(event.target.value);
              video.muted = video.volume === 0;
            }}
            className="hidden w-20 accent-white sm:block"
          />
          <span className="ml-2 text-sm tabular-nums">
            {formatTime(current)} / {formatTime(duration)}
          </span>
          {subtitles && (
            <div className="relative ml-auto">
              <button
                type="button"
                onClick={() => setIsMenuOpen((open) => !open)}
                aria-label="Subtitle"
                aria-haspopup="menu"
                aria-expanded={isMenuOpen}
                className={`${button} ${isSubtitleOn ? 'text-white' : 'text-white/60'}`}
              >
                <Captions className="h-5 w-5" aria-hidden="true" />
                {isSubtitleOn && (
                  <span className="absolute bottom-2 left-1/2 h-0.5 w-5 -translate-x-1/2 rounded bg-red-600" />
                )}
              </button>
              {isMenuOpen && (
                <div
                  role="menu"
                  aria-label="Bahasa subtitle"
                  className="absolute bottom-full right-0 mb-2 min-w-48 rounded-md bg-gray-900/95 py-1 text-sm shadow-lg"
                >
                  {subtitles.options.map((option) => {
                    const isChosen = option.choice === subtitles.choice;
                    const isWorking = option.state === 'queued' || option.state === 'running';
                    const note = isWorking
                      ? option.progress === null
                        ? 'nunggu antrean'
                        : `lagi ${option.isRedoing ? 'disantaiin' : 'diterjemahin'} ${option.progress}%`
                      : option.state === 'failed'
                        ? 'gagal, klik lagi'
                        : null;
                    return (
                      <button
                        key={option.choice}
                        type="button"
                        role="menuitemradio"
                        aria-checked={isChosen}
                        onClick={() => {
                          subtitles.onChoose(option.choice);
                          setIsMenuOpen(false);
                        }}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-white hover:bg-white/10 focus-visible:bg-white/10 focus-visible:outline-none"
                      >
                        <Check className={`h-4 w-4 shrink-0 ${isChosen ? '' : 'invisible'}`} aria-hidden="true" />
                        <span>{option.label}</span>
                        {note && (
                          <span className="ml-auto flex items-center gap-1 pl-3 text-xs text-white/60">
                            {isWorking && (
                              <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                            )}
                            {note}
                          </span>
                        )}
                      </button>
                    );
                  })}
                  {subtitles.claude && (
                    <>
                      <div role="separator" className="my-1 border-t border-white/15" />
                      <button
                        type="button"
                        role="menuitem"
                        disabled={subtitles.claude.isWorking}
                        onClick={() => {
                          subtitles.claude?.onRedo();
                          setIsMenuOpen(false);
                        }}
                        title="Terjemahin ulang pakai Claude biar bahasanya lebih natural. Hasilnya gantiin terjemahan sekarang."
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-white hover:bg-white/10 focus-visible:bg-white/10 focus-visible:outline-none disabled:text-white/60 disabled:hover:bg-transparent"
                      >
                        {subtitles.claude.isWorking ? (
                          <Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                        ) : (
                          <span className="h-4 w-4 shrink-0" aria-hidden="true" />
                        )}
                        <span>
                          {subtitles.claude.isWorking
                            ? `Claude lagi nyantaiin${subtitles.claude.progress === null ? '…' : ` ${subtitles.claude.progress}%`}`
                            : 'Bikin lebih santai (Claude)'}
                        </span>
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          )}
          <button
            type="button"
            onClick={onToggleFullscreen}
            aria-label={isFullscreen ? 'Keluar layar penuh' : 'Layar penuh'}
            className={`${button} ${subtitles ? '' : 'ml-auto'}`}
          >
            {isFullscreen ? (
              <Minimize className="h-5 w-5" aria-hidden="true" />
            ) : (
              <Maximize className="h-5 w-5" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
    </>
  );
};

export default VideoControls;
