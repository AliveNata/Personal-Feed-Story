import React, { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Maximize2, Minimize2, X, ZoomIn, ZoomOut } from 'lucide-react';
import { MediaItem } from '../types';
import { formatBytes } from '../utils/api';
import { ConfirmDelete, focusRing, secondaryButton } from './ui';
import ZoomableImage from './ZoomableImage';
import { MAX_SCALE, useZoom } from './zoom';

const ZOOM_STEP = 1.5;

interface MediaViewerProps {
  item: MediaItem;
  position: number;
  total: number;
  onClose: () => void;
  onPrevious?: () => void;
  onNext?: () => void;
  onDelete: (item: MediaItem) => Promise<void>;
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
  onDelete,
}) => {
  const dialogRef = useRef<HTMLDialogElement>(null);
  // Chrome refuses full screen on a <dialog> itself, so the content box inside goes full screen.
  const stageRef = useRef<HTMLDivElement>(null);
  const { zoom, setZoom, reset, zoomAt } = useZoom();
  const [isFullscreen, setIsFullscreen] = useState(false);
  const isPhoto = item.mediaType === 'photo';

  // A new file starts unzoomed.
  useEffect(() => reset(), [item.id, reset]);

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
      if (event.key === 'ArrowLeft' && onPrevious) onPrevious();
      if (event.key === 'ArrowRight' && onNext) onNext();
      if (event.key === 'f') toggleFullscreen();
      if (!isPhoto) return;
      if (event.key === '+' || event.key === '=') zoomAt(ZOOM_STEP);
      if (event.key === '-') zoomAt(1 / ZOOM_STEP);
      if (event.key === '0') reset();
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
            {isPhoto && (
              <>
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
              </>
            )}
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
          {item.mediaType === 'video' ? (
            <video
              key={item.id}
              src={item.fileUrl}
              poster={item.thumbnailUrl ?? undefined}
              controls
              playsInline
              className="max-h-full max-w-full"
            />
          ) : (
            <ZoomableImage
              key={item.id}
              src={item.fileUrl}
              alt={item.caption || `Foto dari @${item.authorUsername}`}
              zoom={zoom}
              setZoom={setZoom}
              zoomAt={zoomAt}
            />
          )}
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
            <ConfirmDelete
              key={item.id}
              label="Hapus file"
              confirmText="Hapus file ini dari folder?"
              onConfirm={() => onDelete(item)}
            />
          </div>
        </div>
      </div>
    </dialog>
  );
};

export default MediaViewer;
