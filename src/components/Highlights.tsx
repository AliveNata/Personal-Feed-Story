import React, { useEffect, useState } from 'react';
import { Highlight, Post } from '../types';
import { ApiError, fetchHighlightItems, fetchHighlights } from '../utils/api';
import Story from './Story';
import { SectionSelectAll } from './DownloadStatus';
import { ItemDownloadState } from './downloadState';
import { focusRing, secondaryButton } from './ui';

interface HighlightsProps {
  username: string;
  isSelectable: boolean;
  selected: Set<string>;
  savedIn: Record<string, string[]>;
  downloadStates: Record<string, ItemDownloadState>;
  isLocked: (id: string) => boolean;
  isPending: (id: string) => boolean;
  onToggle: (id: string) => void;
  onSelectMany: (ids: string[], select: boolean) => void;
  onItemsLoaded: (items: Post[]) => void;
}

type ListState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'login_required' | 'none' }
  | { status: 'available'; highlights: Highlight[] };

type ItemsState =
  | { status: 'idle' }
  | { status: 'loading'; highlight: Highlight }
  | { status: 'error'; highlight: Highlight; message: string }
  | { status: 'ready'; highlight: Highlight; items: Post[] };

/** Cropped cover first, then the full cover, then the title initial. */
const HighlightCover: React.FC<{ highlight: Highlight; isActive: boolean }> = ({ highlight, isActive }) => {
  const sources = [highlight.coverUrl, highlight.fallbackCoverUrl].filter((url): url is string => !!url);
  const [attempt, setAttempt] = useState(0);
  const ring = `h-16 w-16 rounded-full ring-2 ring-offset-2 ${isActive ? 'ring-gray-900' : 'ring-gray-300'}`;

  if (attempt >= sources.length) {
    return (
      <span aria-hidden="true" className={`${ring} flex items-center justify-center bg-gray-300 text-xl font-bold text-gray-800`}>
        {highlight.title.trim().charAt(0).toUpperCase() || '?'}
      </span>
    );
  }
  return (
    <img
      src={sources[attempt]}
      alt=""
      loading="lazy"
      onError={() => setAttempt((current) => current + 1)}
      className={`${ring} object-cover bg-gray-200`}
    />
  );
};

const errorText = (error: unknown, fallback: string) =>
  error instanceof ApiError ? error.message : fallback;

const Highlights: React.FC<HighlightsProps> = ({
  username,
  isSelectable,
  selected,
  savedIn,
  downloadStates,
  isLocked,
  isPending,
  onToggle,
  onSelectMany,
  onItemsLoaded,
}) => {
  const [list, setList] = useState<ListState>({ status: 'idle' });
  const [items, setItems] = useState<ItemsState>({ status: 'idle' });

  // Loaded on demand: each load costs Instagram requests, so only when the user asks.
  useEffect(() => {
    setList({ status: 'idle' });
    setItems({ status: 'idle' });
  }, [username]);

  const loadList = () => {
    setList({ status: 'loading' });
    fetchHighlights(username)
      .then((response) =>
        setList(
          response.status === 'available'
            ? { status: 'available', highlights: response.highlights }
            : { status: response.status },
        ),
      )
      .catch((error: unknown) => {
        console.error('fetchHighlights failed', error);
        setList({ status: 'error', message: errorText(error, 'Gagal memuat highlight') });
      });
  };

  const openHighlight = (highlight: Highlight) => {
    setItems({ status: 'loading', highlight });
    fetchHighlightItems(username, highlight.id)
      .then((response) => {
        setItems({ status: 'ready', highlight, items: response.items });
        onItemsLoaded(response.items);
      })
      .catch((error: unknown) => {
        console.error('fetchHighlightItems failed', error);
        setItems({ status: 'error', highlight, message: errorText(error, 'Gagal memuat isi highlight') });
      });
  };

  const activeId = items.status === 'idle' ? null : items.highlight.id;

  return (
    <section className="mb-8" aria-labelledby="highlights-title">
      <h2 id="highlights-title" className="text-2xl font-bold mb-4">
        Highlights
      </h2>

      {list.status === 'idle' && (
        <button type="button" className={secondaryButton} onClick={loadList}>
          Tampilkan highlight
        </button>
      )}
      {list.status === 'loading' && (
        <p role="status" className="text-gray-700">
          Memuat highlight...
        </p>
      )}
      {list.status === 'error' && (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-red-700">{list.message}</p>
          <button type="button" className={secondaryButton} onClick={loadList}>
            Coba lagi
          </button>
        </div>
      )}
      {list.status === 'none' && <p className="text-gray-700">Akun ini tidak punya highlight.</p>}
      {list.status === 'login_required' && (
        <p className="text-gray-700">Highlight butuh session Instagram di backend (IG_USERNAME).</p>
      )}

      {list.status === 'available' && (
        <ul className="flex gap-4 overflow-x-auto pb-2">
          {list.highlights.map((highlight) => (
            <li key={highlight.id} className="shrink-0">
              <button
                type="button"
                onClick={() => openHighlight(highlight)}
                aria-pressed={activeId === highlight.id}
                className={`flex w-20 flex-col items-center gap-1 rounded-md p-1 ${focusRing}`}
              >
                <HighlightCover highlight={highlight} isActive={activeId === highlight.id} />
                <span className="w-full truncate text-center text-xs text-gray-800">{highlight.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {items.status === 'loading' && (
        <p role="status" className="mt-4 text-gray-700">
          Memuat isi "{items.highlight.title}"...
        </p>
      )}
      {items.status === 'error' && (
        <div role="alert" className="mt-4 flex flex-wrap items-center gap-3">
          <p className="text-red-700">{items.message}</p>
          <button type="button" className={secondaryButton} onClick={() => openHighlight(items.highlight)}>
            Coba lagi
          </button>
        </div>
      )}
      {items.status === 'ready' && items.items.length === 0 && (
        <p className="mt-4 text-gray-700">Highlight "{items.highlight.title}" kosong.</p>
      )}
      {items.status === 'ready' && items.items.length > 0 && (
        <div className="mt-4">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <p className="text-sm text-gray-700">
              {items.highlight.title}, {items.items.length} item
            </p>
            {isSelectable && (
              <SectionSelectAll
                ids={items.items.map((item) => item.id)}
                selected={selected}
                isLocked={isLocked}
                isPending={isPending}
                noun="highlight"
                onChange={onSelectMany}
              />
            )}
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-4">
            {items.items.map((item) => (
              <Story
                key={item.id}
                story={item}
                isSelectable={isSelectable}
                isSelected={selected.has(item.id)}
                savedFolders={savedIn[item.id]}
                downloadState={downloadStates[item.id]}
                onToggle={onToggle}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
};

export default Highlights;
