import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Heart, MessageCircle, Share2 } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import FavoriteToggle from '../favorites/FavoriteToggle';
import { useFavorites } from '../favorites/FavoritesContext';
import { Platform, Post, RateInfo, StoriesStatus } from '../types';
import { formatRelativeTime, lookupDownloaded } from '../utils/api';
import DownloadBar from './DownloadBar';
import Highlights from './Highlights';
import PostCarousel from './PostCarousel';
import RateNotice from './RateNotice';
import SelectToggle from './SelectToggle';
import Story from './Story';
import { focusRing, primaryButton, secondaryButton } from './ui';

export type FeedStatus = 'idle' | 'loading' | 'success' | 'error';

interface FeedProps {
  status: FeedStatus;
  posts: Post[];
  platform: Platform;
  username: string;
  errorMessage: string | null;
  cached: boolean;
  storiesStatus: StoriesStatus;
  rate: RateInfo | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreError: string | null;
  onRetry: () => void;
  onLoadMore: () => void;
}

const numberFormat = new Intl.NumberFormat('id');

const STORIES_MESSAGES: Record<StoriesStatus, string | null> = {
  available: null,
  none: 'Tidak ada story aktif dalam 24 jam terakhir.',
  login_required: 'Story butuh session Instagram di backend (IG_USERNAME).',
  not_supported: 'Story TikTok belum didukung.',
};

const Feed: React.FC<FeedProps> = ({
  status,
  posts,
  platform,
  username,
  errorMessage,
  cached,
  storiesStatus,
  rate,
  hasMore,
  isLoadingMore,
  loadMoreError,
  onRetry,
  onLoadMore,
}) => {
  const platformLabel = platform === 'instagram' ? 'Instagram' : 'TikTok';
  const { user } = useAuth();
  const { markSeen } = useFavorites();
  const markSeenRef = useRef(markSeen);
  markSeenRef.current = markSeen;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // post id -> folders that already hold it. Those posts are shown greyed out and cannot be picked.
  const [savedIn, setSavedIn] = useState<Record<string, string[]>>({});

  // Every post, story and highlight item seen for this account, to refresh their saved state.
  const knownItems = useRef<Map<string, Post>>(new Map());

  // A different account means different post ids, so old state would point at nothing.
  useEffect(() => {
    setSelected(new Set());
    setSavedIn({});
    knownItems.current = new Map();
  }, [platform, username]);

  // Only a fresh load counts as a visit. Reading markSeen through a ref keeps a later
  // favorites refresh from clearing an update the open feed does not show yet.
  useEffect(() => {
    if (status === 'success') markSeenRef.current(platform, username);
  }, [status, platform, username]);

  const lookupSaved = useCallback(
    (items: Post[]) => {
      items.forEach((item) => knownItems.current.set(item.id, item));
      if (!user || items.length === 0) return;
      lookupDownloaded(
        platform,
        items.map((item) => item.id),
      )
        .then((found) => setSavedIn((previous) => ({ ...previous, ...found })))
        .catch((error: unknown) => {
          // Not fatal: the feed still works, posts just are not marked as saved.
          console.error('lookupDownloaded failed', error);
        });
    },
    [user, platform],
  );

  useEffect(() => {
    lookupSaved(posts);
  }, [posts, lookupSaved]);

  const refreshAllSaved = useCallback(
    () => lookupSaved(Array.from(knownItems.current.values())),
    [lookupSaved],
  );

  const toggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });

  if (status === 'idle') {
    return (
      <p className="bg-white rounded-lg shadow-md p-6 text-gray-700">
        Pilih platform dan isi username, terus klik "Ambil feed".
      </p>
    );
  }

  if (status === 'loading') {
    return (
      <p role="status" aria-live="polite" className="bg-white rounded-lg shadow-md p-6 text-gray-700">
        Mengambil feed {platformLabel} @{username}. Scrape pertama bisa makan waktu sampai 1 menit.
      </p>
    );
  }

  if (status === 'error') {
    return (
      <div role="alert" className="bg-white rounded-lg shadow-md p-6 border border-red-300">
        <p className="font-semibold text-red-700 mb-1">Gagal mengambil feed @{username}</p>
        <p className="text-gray-800 mb-4">{errorMessage}</p>
        <button type="button" onClick={onRetry} className={primaryButton}>
          Coba lagi
        </button>
      </div>
    );
  }

  const stories = posts.filter((post) => post.isStory);
  const regularPosts = posts.filter((post) => !post.isStory);
  const selectableIds = posts.filter((post) => !savedIn[post.id]).map((post) => post.id);
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));
  const storiesMessage = STORIES_MESSAGES[storiesStatus];

  return (
    <div className={user ? 'pb-48 md:pb-32' : ''}>
      <RateNotice rate={rate} platformLabel={platformLabel} />

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <p className="text-sm text-gray-700">
          {platformLabel} @{username}, {regularPosts.length} post
          {stories.length ? `, ${stories.length} story` : ''}
          {cached ? ' (dari cache)' : ''}
        </p>
        {user && <FavoriteToggle platform={platform} username={username} />}
        {user ? (
          <button
            type="button"
            className={`ml-auto ${secondaryButton}`}
            disabled={selectableIds.length === 0}
            onClick={() => setSelected(allSelected ? new Set() : new Set(selectableIds))}
          >
            {selectableIds.length === 0
              ? 'Semua sudah di Library'
              : allSelected
                ? 'Batal pilih semua'
                : 'Pilih semua'}
          </button>
        ) : (
          <p className="ml-auto text-sm text-gray-700">
            <Link to="/login?next=/" className={`underline text-gray-900 ${focusRing}`}>
              Login
            </Link>{' '}
            untuk download post ke Library.
          </p>
        )}
      </div>

      <section className="mb-8" aria-labelledby="stories-title">
        <h2 id="stories-title" className="text-2xl font-bold mb-4">
          Stories
        </h2>
        {stories.length > 0 ? (
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-4">
            {stories.map((story) => (
              <Story
                key={story.id}
                story={story}
                isSelectable={user !== null}
                isSelected={selected.has(story.id)}
                savedFolders={savedIn[story.id]}
                onToggle={toggle}
              />
            ))}
          </div>
        ) : (
          <p className="text-gray-700">{storiesMessage ?? 'Tidak ada story.'}</p>
        )}
      </section>

      {platform === 'instagram' && (
        <Highlights
          username={username}
          isSelectable={user !== null}
          selected={selected}
          savedIn={savedIn}
          onToggle={toggle}
          onItemsLoaded={lookupSaved}
        />
      )}

      <h2 className="text-2xl font-bold mb-4">Feed</h2>
      {regularPosts.length === 0 && (
        <p className="mb-4 text-gray-700">Belum ada post di rentang ini.</p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
        {regularPosts.map((post) => {
          const savedFolders = savedIn[post.id];
          return (
            <article
              key={post.id}
              className={`relative bg-white rounded-lg shadow-md overflow-hidden flex flex-col ${
                selected.has(post.id) ? 'ring-4 ring-gray-900' : ''
              }`}
            >
              <PostCarousel post={post} isSaved={savedFolders !== undefined} />
              {user && (
                <SelectToggle
                  isSelected={selected.has(post.id)}
                  isDisabled={savedFolders !== undefined}
                  label={
                    savedFolders
                      ? `Sudah di Library: ${savedFolders.join(', ')}`
                      : `Pilih post ${post.caption.slice(0, 40) || post.id}`
                  }
                  onToggle={() => toggle(post.id)}
                />
              )}
              {savedFolders && (
                <span className="absolute top-12 left-2 z-10 max-w-[70%] truncate rounded bg-gray-900 px-2 py-1 text-xs font-semibold text-white">
                  Di Library: {savedFolders.join(', ')}
                </span>
              )}
              <div className="p-4 flex-1">
                <p className="text-gray-800 mb-2 break-words line-clamp-4">{post.caption}</p>
                <div className="flex items-center gap-6 text-gray-700">
                  <div className="flex items-center" title="Likes">
                    <Heart className="w-5 h-5 mr-1" aria-hidden="true" />
                    <span>{numberFormat.format(post.likes)}</span>
                  </div>
                  <div className="flex items-center" title="Komentar">
                    <MessageCircle className="w-5 h-5 mr-1" aria-hidden="true" />
                    <span>{numberFormat.format(post.comments)}</span>
                  </div>
                  {post.shares !== null && (
                    <div className="flex items-center" title="Shares">
                      <Share2 className="w-5 h-5 mr-1" aria-hidden="true" />
                      <span>{numberFormat.format(post.shares)}</span>
                    </div>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 bg-gray-100 px-4 py-2 text-sm text-gray-700">
                <span>
                  {platformLabel} • <time dateTime={post.createdAt}>{formatRelativeTime(post.createdAt)}</time>
                </span>
                {post.permalink && (
                  <a
                    href={post.permalink}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`ml-auto underline text-gray-900 ${focusRing}`}
                  >
                    Buka post
                  </a>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <div className="mt-8 flex flex-col items-center gap-2">
        {loadMoreError && (
          <p role="alert" className="text-center text-red-700">
            {loadMoreError}
          </p>
        )}
        {hasMore ? (
          <button type="button" className={primaryButton} onClick={onLoadMore} disabled={isLoadingMore}>
            {isLoadingMore
              ? 'Memuat...'
              : platform === 'tiktok'
                ? 'Muat post lebih lama'
                : 'Muat lebih banyak'}
          </button>
        ) : (
          <p className="text-sm text-gray-700">Semua post sudah dimuat.</p>
        )}
      </div>

      {user && (
        <DownloadBar
          platform={platform}
          username={username}
          selectedIds={Array.from(selected)}
          onClearSelection={() => setSelected(new Set())}
          onJobFinished={refreshAllSaved}
        />
      )}
    </div>
  );
};

export default Feed;
