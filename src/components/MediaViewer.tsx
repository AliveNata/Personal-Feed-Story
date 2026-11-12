import React, { useEffect, useRef, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  Repeat,
  RotateCw,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { MediaItem, Rotation } from '../types';
import { MoveResult, formatBytes, setItemRotation } from '../utils/api';
import {
  SLIDESHOW_SPEEDS,
  SlideshowSeconds,
  readRepeatVideo,
  readSlideshowSeconds,
  saveRepeatVideo,
  saveSlideshowSeconds,
} from '../utils/viewerPrefs';
import MoveForm from './MoveForm';
import { ConfirmDelete, focusRing, secondaryButton } from './ui';
import { SEEK_STEP_SECONDS, SeekFlash } from './VideoSeekZones';
import ZoomableMedia from './ZoomableMedia';
import { MAX_SCALE, useZoom } from './zoom';

const ZOOM_STEP = 1.5;

interface MediaViewerProps {
  item: MediaItem;
  position: number;
  total: number;
  onClose: () => void;
  onPrevious?: () => void;
  onNext?: () => void;
  // Slideshow step: like onNext, but wraps from the last file back to the first.
  onSlideshowNext?: () => void;
  onDelete: (item: MediaItem) => Promise<void>;
  currentFolderId: number;
  onMoved: (result: MoveResult, item: MediaItem) => void;
  // The item changed in place, such as a new rotation.
  onItemUpdated: (item: MediaItem) => void;
}

const SOURCE_LABELS: Record<MediaItem['platform'], string> = {
  tiktok: 'TikTok',
  instagram: 'Instagram',
  upload: 'Upload manual',
};

const dateFormat = new Intl.DateTimeFormat('id', { dateStyle: 'medium', timeStyle: 'short' });

const MediaViewer: React.FC<MediaViewerProps> = ({
  item,
  position,
  total,
  onClose,
  onPrevious,
  onNext,
  onSlideshowNext,
  onDelete,
  currentFolderId,
  onMoved,
  onItemUpdated,
}) => {
  const dialogRef = useRef<HTMLDialogElement>(null);
  // Chrome refuses full screen on a <dialog> itself, so the content box inside goes full screen.
  const stageRef = useRef<HTMLDivElement>(null);
  const { zoom, setZoom, reset, zoomAt } = useZoom();
  const [isFullscreen, setIsFullscreen] = useState(false);
  const isVideo = item.mediaType === 'video';
  const [isMoving, setIsMoving] = useState(false);
  useEffect(() => setIsMoving(false), [item.id]);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);

  // Track play state for the toolbar button, which stands in for the hidden native controls
  // while a video is zoomed.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const sync = () => setIsPlaying(!video.paused);
    video.addEventListener('play', sync);
    video.addEventListener('pause', sync);
    sync();
    return () => {
      video.removeEventListener('play', sync);
      video.removeEventListener('pause', sync);
    };
  }, [item.id]);

  const [repeatVideo, setRepeatVideo] = useState(readRepeatVideo);
  // Set when a video ends and the next one opens, so that one starts playing on its own.
  const [continuePlaying, setContinuePlaying] = useState(false);

  const toggleRepeat = () => {
    setRepeatVideo((previous) => {
      saveRepeatVideo(!previous);
      return !previous;
    });
  };

  const handleVideoEnded = () => {
    // With repeat on the <video> loops by itself and never fires ended.
    if (!onNext) return;
    setContinuePlaying(true);
    onNext();
  };

  const [isSlideshowOn, setIsSlideshowOn] = useState(false);
  const [slideshowSeconds, setSlideshowSeconds] = useState<SlideshowSeconds>(readSlideshowSeconds);
  const canSlideshow = !isVideo && total > 1 && onSlideshowNext !== undefined;
  const isZoomed = zoom.scale > 1;

  // The parent passes a new function every render, which must not restart the countdown.
  const slideshowNextRef = useRef(onSlideshowNext);
  slideshowNextRef.current = onSlideshowNext;

  // One timer per photo: navigating by hand restarts the count, zooming in holds it.
  useEffect(() => {
    if (!isSlideshowOn || !canSlideshow || isZoomed) return;
    const timer = window.setTimeout(() => slideshowNextRef.current?.(), slideshowSeconds * 1000);
    return () => window.clearTimeout(timer);
  }, [isSlideshowOn, canSlideshow, isZoomed, slideshowSeconds, item.id]);

  const toggleSlideshow = () => setIsSlideshowOn((previous) => !previous);

  const changeSlideshowSeconds = (seconds: SlideshowSeconds) => {
    setSlideshowSeconds(seconds);
    saveSlideshowSeconds(seconds);
  };

  const [seekFlash, setSeekFlash] = useState<SeekFlash | null>(null);
  const lastSeek = useRef<{ side: SeekFlash['side']; seconds: number; at: number } | null>(null);

  // Repeated seeks in the same direction add up in the bubble ("+15 detik"), like phone players.
  const seek = (seconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    const end = Number.isFinite(video.duration) ? video.duration : video.currentTime + seconds;
    video.currentTime = Math.min(Math.max(video.currentTime + seconds, 0), end);
    const side = seconds < 0 ? 'back' : 'forward';
    const now = performance.now();
    const previous = lastSeek.current;
    const total =
      previous && previous.side === side && now - previous.at < 1000
        ? previous.seconds + Math.abs(seconds)
        : Math.abs(seconds);
    lastSeek.current = { side, seconds: total, at: now };
    setSeekFlash({ side, seconds: total, key: now });
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (video) void (video.paused ? video.play() : video.pause());
  };

  // A new file starts unzoomed.
  useEffect(() => reset(), [item.id, reset]);

  // Saved right away and shown before the server answers, put back if saving fails.
  const rotate = () => {
    const previous = item;
    const next = ((item.rotation + 90) % 360) as Rotation;
    reset();
    onItemUpdated({ ...item, rotation: next });
    setItemRotation(item.id, next)
      .then(onItemUpdated)
      .catch((caught: unknown) => {
        console.error('setItemRotation failed', caught);
        onItemUpdated(previous);
      });
  };

  useEffect(() => {
    const sync = () => setIsFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);

  const toggleFullscreen = () => {
    const request = document.fullscreenElement
      ? document.exitFullscreen()
      : stageRef.current?.requestFullscreen();
    request?.catch((error: unknown) => console.error('fullscreen toggle failed', error));
  };

  // Native <dialog>: showModal gives focus trapping, Escape to close and an inert background.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) {
      dialog.showModal();
    }
  }, []);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      // Arrow keys inside the video player seek the video, so leave them alone there.
      if (target.tagName === 'VIDEO') return;
      // Typing in a field is not a shortcut, and buttons and links handle Space themselves.
      if (target.closest('input, textarea')) return;
      if (event.key === ' ' && target.closest('button, a')) return;
      if (isVideo && !event.shiftKey) {
        // YouTube style keys: arrows 5 s, J and L 10 s, K or Space play and pause, M mute.
        const keyActions: Record<string, () => void> = {
          ArrowLeft: () => seek(-SEEK_STEP_SECONDS),
          ArrowRight: () => seek(SEEK_STEP_SECONDS),
          j: () => seek(-10),
          l: () => seek(10),
          k: togglePlay,
          ' ': togglePlay,
          m: () => {
            if (videoRef.current) videoRef.current.muted = !videoRef.current.muted;
          },
        };
        const action = keyActions[event.key];
        if (action) {
          event.preventDefault();
          action();
          return;
        }
      }
      if (!isVideo && canSlideshow && event.key === ' ') {
        event.preventDefault();
        toggleSlideshow();
        return;
      }
      // On a video, Shift plus an arrow moves between files.
      if (event.key === 'ArrowLeft' && onPrevious) onPrevious();
      if (event.key === 'ArrowRight' && onNext) onNext();
      if (event.key === 'f') toggleFullscreen();
      if (event.key === '+' || event.key === '=') zoomAt(ZOOM_STEP);
      if (event.key === '-') zoomAt(1 / ZOOM_STEP);
      if (event.key === '0') reset();
      if (event.key === 'r') rotate();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  });

  const navButton = `flex h-11 w-11 items-center justify-center rounded-full bg-white/90 text-gray-900 disabled:opacity-30 ${focusRing}`;

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      aria-label={`File ${position} dari ${total}`}
      className="m-0 h-full max-h-none w-full max-w-none bg-black/90 p-0 backdrop:bg-black/60"
    >
      <div ref={stageRef} className={`flex h-full flex-col ${isFullscreen ? 'bg-black' : ''}`}>
        <div className="flex items-center justify-between gap-2 p-3 text-white">
          <span className="text-sm">
            {position} / {total}
          </span>
          <div className="flex items-center gap-2">
            {isVideo && (
              <button
                type="button"
                onClick={toggleRepeat}
                aria-pressed={repeatVideo}
                aria-label="Ulangi video"
                title={repeatVideo ? 'Ulangi video: nyala' : 'Ulangi video: mati, lanjut ke video berikutnya'}
                className={`${navButton} ${repeatVideo ? '!bg-blue-600 !text-white' : ''}`}
              >
                <Repeat className="h-5 w-5" aria-hidden="true" />
              </button>
            )}
            {canSlideshow && (
              <>
                <button
                  type="button"
                  onClick={toggleSlideshow}
                  aria-pressed={isSlideshowOn}
                  aria-label={isSlideshowOn ? 'Jeda slideshow' : 'Mulai slideshow'}
                  className={`${navButton} ${isSlideshowOn ? '!bg-blue-600 !text-white' : ''}`}
                >
                  {isSlideshowOn ? (
                    <Pause className="h-5 w-5" aria-hidden="true" />
                  ) : (
                    <Play className="h-5 w-5" aria-hidden="true" />
                  )}
                </button>
                <label htmlFor="slideshow-speed" className="sr-only">
                  Kecepatan slideshow
                </label>
                <select
                  id="slideshow-speed"
                  value={slideshowSeconds}
                  onChange={(event) => changeSlideshowSeconds(Number(event.target.value) as SlideshowSeconds)}
                  className={`h-11 rounded-full bg-white/90 px-3 text-sm text-gray-900 ${focusRing}`}
                >
                  {SLIDESHOW_SPEEDS.map((seconds) => (
                    <option key={seconds} value={seconds}>
                      {seconds} dtk
                    </option>
                  ))}
                </select>
              </>
            )}
            {isVideo && zoom.scale > 1 && (
              <button
                type="button"
                onClick={togglePlay}
                aria-label={isPlaying ? 'Jeda video' : 'Putar video'}
                className={navButton}
              >
                {isPlaying ? (
                  <Pause className="h-5 w-5" aria-hidden="true" />
                ) : (
                  <Play className="h-5 w-5" aria-hidden="true" />
                )}
              </button>
            )}
            {/* Phones pinch to zoom, so the buttons only take toolbar room there. */}
            <div className="hidden items-center gap-2 sm:flex">
              <button
                type="button"
                onClick={() => zoomAt(1 / ZOOM_STEP)}
                disabled={zoom.scale <= 1}
                aria-label="Perkecil"
                className={navButton}
              >
                <ZoomOut className="h-5 w-5" aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={reset}
                aria-label="Ukuran asli"
                className={`min-w-[3.5rem] rounded-full px-2 text-sm tabular-nums text-white ${focusRing}`}
              >
                {Math.round(zoom.scale * 100)}%
              </button>
              <button
                type="button"
                onClick={() => zoomAt(ZOOM_STEP)}
                disabled={zoom.scale >= MAX_SCALE}
                aria-label="Perbesar"
                className={navButton}
              >
                <ZoomIn className="h-5 w-5" aria-hidden="true" />
              </button>
            </div>
            <button
              type="button"
              onClick={rotate}
              aria-label="Putar 90 derajat"
              title="Putar 90 derajat searah jarum jam (R)"
              className={navButton}
            >
              <RotateCw className="h-5 w-5" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={toggleFullscreen}
              aria-label={isFullscreen ? 'Keluar layar penuh' : 'Layar penuh'}
              aria-pressed={isFullscreen}
              className={navButton}
            >
              {isFullscreen ? (
                <Minimize2 className="h-5 w-5" aria-hidden="true" />
              ) : (
                <Maximize2 className="h-5 w-5" aria-hidden="true" />
              )}
            </button>
            <button type="button" onClick={onClose} aria-label="Tutup" className={navButton}>
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="relative flex min-h-0 flex-1 items-center justify-center px-2">
          <ZoomableMedia
            key={item.id}
            kind={item.mediaType}
            src={item.fileUrl}
            alt={item.caption || `${isVideo ? 'Video' : 'Foto'} dari @${item.authorUsername}`}
            poster={item.thumbnailUrl ?? undefined}
            videoRef={videoRef}
            loop={repeatVideo}
            autoPlay={continuePlaying}
            onEnded={handleVideoEnded}
            zoom={zoom}
            setZoom={setZoom}
            zoomAt={zoomAt}
            onSeek={seek}
            onTogglePlay={togglePlay}
            onToggleFullscreen={toggleFullscreen}
            seekFlash={seekFlash}
            rotation={item.rotation}
          />
          <button
            type="button"
            onClick={onPrevious}
            disabled={!onPrevious}
            aria-label="Sebelumnya"
            className={`absolute left-2 top-1/2 -translate-y-1/2 ${navButton}`}
          >
            <ChevronLeft className="h-6 w-6" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={!onNext}
            aria-label="Berikutnya"
            className={`absolute right-2 top-1/2 -translate-y-1/2 ${navButton}`}
          >
            <ChevronRight className="h-6 w-6" aria-hidden="true" />
          </button>
        </div>

        {/* Full screen is for the photo: the details panel comes back on exit. */}
        <div className={`bg-white p-4 ${isFullscreen ? 'hidden' : ''}`}>
          <p className="text-sm text-gray-700">
            {item.platform === 'upload' ? '' : `@${item.authorUsername} · `}
            {SOURCE_LABELS[item.platform]}
            {item.isStory ? ' story' : ''}
            {item.postedAt ? ` · ${dateFormat.format(new Date(item.postedAt))}` : ''}
            {' · '}
            {formatBytes(item.sizeBytes)}
          </p>
          {item.caption && <p className="mt-1 text-gray-900 break-words line-clamp-3">{item.caption}</p>}
          {isVideo && (
            // Phones get the tap zones, keyboards get the usual player keys.
            <p className="mt-1 hidden text-xs text-gray-700 md:block">
              Keyboard: ← → 5 detik, J L 10 detik, K atau Spasi putar/jeda, M bisu, F layar penuh, Shift + ← →
              file lain. Klik 2x di kiri/kanan video juga bisa buat layar penuh. Tombol ulang mati: habis
              video langsung lanjut ke video berikutnya.
            </p>
          )}
          {canSlideshow && (
            <p className="mt-1 hidden text-xs text-gray-700 md:block">
              Keyboard: Spasi mulai atau jeda slideshow, ← → foto lain.
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <a
              href={`${item.fileUrl}?download=true`}
              className={`inline-flex items-center ${secondaryButton}`}
            >
              Download file
            </a>
            {item.permalink && (
              <a
                href={item.permalink}
                target="_blank"
                rel="noopener noreferrer"
                className={`inline-flex items-center ${secondaryButton}`}
              >
                Buka post asli
              </a>
            )}
            <button type="button" className={secondaryButton} onClick={() => setIsMoving((open) => !open)}>
              Pindahkan
            </button>
            <ConfirmDelete
              key={item.id}
              label="Hapus file"
              confirmText="Hapus file ini dari folder?"
              onConfirm={() => onDelete(item)}
            />
          </div>
          {isMoving && (
            <div className="mt-3">
              <MoveForm
                itemIds={[item.id]}
                currentFolderId={currentFolderId}
                inputId="move-folder-viewer"
                onMoved={(result) => onMoved(result, item)}
                onCancel={() => setIsMoving(false)}
              />
            </div>
          )}
        </div>
      </div>
    </dialog>
  );
};

export default MediaViewer;
