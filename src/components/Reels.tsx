import React, { useEffect, useState } from 'react';
import { Post, RateInfo } from '../types';
import { ApiError, fetchReels } from '../utils/api';
import { SectionSelectAll } from './DownloadStatus';
import { ItemDownloadState } from './downloadState';
import RateNotice from './RateNotice';
import Story from './Story';
import { secondaryButton } from './ui';

interface ReelsProps {
  username: string;
  isSelectable: boolean;
  selected: Set<string>;
  savedIn: Record<string, string[]>;
  downloadStates: Record<string, ItemDownloadState>;
  percentFor: (id: string) => number | null;
  isLocked: (id: string) => boolean;
  isPending: (id: string) => boolean;
  onToggle: (id: string) => void;
  onSelectMany: (ids: string[], select: boolean) => void;
  onItemsLoaded: (items: Post[]) => void;
}

type ReelsState =
  | { status: 'idle' | 'loading' }
  | { status: 'error'; message: string }
  | { status: 'login_required' | 'none' }
  | {
      status: 'available';
      reels: Post[];
      nextCursor: string | null;
      isLoadingMore: boolean;
      moreError: string | null;
    };

const errorText = (error: unknown) => (error instanceof ApiError ? error.message : 'Gagal memuat reels');

const Reels: React.FC<ReelsProps> = ({
  username,
  isSelectable,
  selected,
  savedIn,
  downloadStates,
  percentFor,
  isLocked,
  isPending,
  onToggle,
  onSelectMany,
  onItemsLoaded,
}) => {
  const [state, setState] = useState<ReelsState>({ status: 'idle' });
  const [rate, setRate] = useState<RateInfo | null>(null);

  // On demand only: every page costs Instagram one query per reel.
  useEffect(() => {
    setState({ status: 'idle' });
    setRate(null);
  }, [username]);

  const loadFirst = () => {
    setState({ status: 'loading' });
    fetchReels(username, null)
      .then((response) => {
        setRate(response.rate);
        if (response.status !== 'available') {
          setState({ status: response.status });
          return;
        }
        setState({
          status: 'available',
          reels: response.reels,
          nextCursor: response.nextCursor,
          isLoadingMore: false,
          moreError: null,
        });
        onItemsLoaded(response.reels);
      })
      .catch((error: unknown) => {
        console.error('fetchReels failed', error);
        setState({ status: 'error', message: errorText(error) });
      });
  };

  const loadMore = () => {
    if (state.status !== 'available' || !state.nextCursor || state.isLoadingMore) return;
    const cursor = state.nextCursor;
    setState({ ...state, isLoadingMore: true, moreError: null });
    fetchReels(username, cursor)
      .then((response) => {
        setRate(response.rate);
        setState((previous) => {
          if (previous.status !== 'available') return previous;
          const seen = new Set(previous.reels.map((reel) => reel.id));
          return {
            ...previous,
            reels: [...previous.reels, ...response.reels.filter((reel) => !seen.has(reel.id))],
            nextCursor: response.nextCursor,
            isLoadingMore: false,
          };
        });
        onItemsLoaded(response.reels);
      })
      .catch((error: unknown) => {
        console.error('fetchReels (more) failed', error);
        setState((previous) =>
          previous.status === 'available'
            ? { ...previous, isLoadingMore: false, moreError: errorText(error) }
            : previous,
        );
      });
  };

  return (
    <section className="mb-8" aria-labelledby="reels-title">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 id="reels-title" className="text-2xl font-bold">
          Reels
        </h2>
        {isSelectable && state.status === 'available' && (
          <SectionSelectAll
            ids={state.reels.map((reel) => reel.id)}
            selected={selected}
            isLocked={isLocked}
            isPending={isPending}
            noun="reel"
            onChange={onSelectMany}
          />
        )}
      </div>
      <RateNotice rate={rate} platformLabel="Instagram" />

      {state.status === 'idle' && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={secondaryButton} onClick={loadFirst}>
            Tampilkan reels
          </button>
          <p className="text-sm text-gray-700">
            Dimuat 6 per halaman, tiap halaman makan sekitar 8 request Instagram.
          </p>
        </div>
      )}
      {state.status === 'loading' && (
        <p role="status" className="text-gray-700">
          Memuat reels... bisa sampai setengah menit karena tiap reel diambil satu per satu.
        </p>
      )}
      {state.status === 'error' && (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-red-700">{state.message}</p>
          <button type="button" className={secondaryButton} onClick={loadFirst}>
            Coba lagi
          </button>
        </div>
      )}
      {state.status === 'none' && <p className="text-gray-700">Akun ini tidak punya reels.</p>}
      {state.status === 'login_required' && (
        <p className="text-gray-700">Reels butuh session Instagram di backend (IG_USERNAME).</p>
      )}

      {state.status === 'available' && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-4">
            {state.reels.map((reel) => (
              <Story
                key={reel.id}
                story={reel}
                kind="reel"
                isSelectable={isSelectable}
                isSelected={selected.has(reel.id)}
                savedFolders={savedIn[reel.id]}
                downloadState={downloadStates[reel.id]}
                downloadPercent={percentFor(reel.id)}
                onToggle={onToggle}
              />
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            {state.nextCursor ? (
              <button
                type="button"
                className={secondaryButton}
                onClick={loadMore}
                disabled={state.isLoadingMore}
              >
                {state.isLoadingMore ? 'Memuat reels...' : 'Muat reels lagi'}
              </button>
            ) : (
              <p className="text-sm text-gray-700">Semua reels sudah dimuat.</p>
            )}
            {state.moreError && (
              <p role="alert" className="text-sm text-red-700">
                {state.moreError}
              </p>
            )}
          </div>
        </>
      )}
    </section>
  );
};

export default Reels;
