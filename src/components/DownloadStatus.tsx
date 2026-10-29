import React from 'react';
import { Loader2 } from 'lucide-react';
import { ItemDownloadState } from './downloadState';
import { secondaryButton } from './ui';

const LABELS: Record<ItemDownloadState, string> = {
  queued: 'Antre',
  downloading: 'Mengunduh...',
  done: '',
  failed: 'Gagal',
};

/** Covers a card while its post is in the job. Text label too, so it still reads with motion off. */
interface DownloadOverlayProps {
  state: ItemDownloadState | undefined;
  // Only for the post being downloaded, null until the first bytes arrive.
  percent?: number | null;
  className?: string;
}

export const DownloadOverlay: React.FC<DownloadOverlayProps> = ({
  state,
  percent = null,
  className = '',
}) => {
  if (!state || state === 'done') return null;
  return (
    <div
      // DownloadBar announces progress, a live region per card would flood screen readers.
      aria-hidden="true"
      className={`absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-sm font-semibold text-white ${className} ${
        state === 'failed' ? 'bg-red-900/60' : 'bg-gray-900/55'
      }`}
    >
      {state === 'downloading' && (
        <Loader2 aria-hidden="true" className="h-8 w-8 animate-spin motion-reduce:animate-none" />
      )}
      <span className="rounded bg-gray-900/80 px-2 py-1">
        {/* No percent yet means the pause between posts or the link lookup, not bytes moving. */}
        {state === 'downloading' && percent === null ? 'Menyiapkan...' : LABELS[state]}
      </span>
      {state === 'downloading' && percent !== null && (
        <span className="flex w-3/5 max-w-[10rem] flex-col items-center gap-1">
          <span className="text-base tabular-nums">{percent}%</span>
          <span className="block h-1.5 w-full overflow-hidden rounded-full bg-white/30">
            <span
              className="block h-full rounded-full bg-white transition-[width] duration-700 ease-out motion-reduce:transition-none"
              style={{ width: `${percent}%` }}
            />
          </span>
        </span>
      )}
    </div>
  );
};

interface SectionSelectAllProps {
  ids: string[];
  selected: Set<string>;
  // Saved or already in the job: cannot be picked.
  isLocked: (id: string) => boolean;
  // In the running job: locked for now, but not in the Library yet.
  isPending: (id: string) => boolean;
  noun: string;
  onChange: (ids: string[], select: boolean) => void;
}

export const SectionSelectAll: React.FC<SectionSelectAllProps> = ({
  ids,
  selected,
  isLocked,
  isPending,
  noun,
  onChange,
}) => {
  const pickable = ids.filter((id) => !isLocked(id));
  if (ids.length === 0) return null;
  const allPicked = pickable.length > 0 && pickable.every((id) => selected.has(id));
  return (
    <button
      type="button"
      className={secondaryButton}
      disabled={pickable.length === 0}
      onClick={() => onChange(pickable, !allPicked)}
    >
      {pickable.length === 0
        ? ids.some(isPending)
          ? `${noun[0].toUpperCase()}${noun.slice(1)} sedang diunduh`
          : `Semua ${noun} sudah di Library`
        : allPicked
          ? `Batal pilih ${noun}`
          : `Pilih semua ${noun}`}
    </button>
  );
};
