import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Heart, MessageCircle, Share2 } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { Platform, Post } from '../types';
import { formatRelativeTime, lookupDownloaded } from '../utils/api';
import DownloadBar from './DownloadBar';
import SelectToggle from './SelectToggle';
import Story from './Story';
import { focusRing, secondaryButton } from './ui';

export type FeedStatus = 'idle' | 'loading' | 'success' | 'error';

interface FeedProps {
  status: FeedStatus;
  posts: Post[];
  platform: Platform;
  username: string;
  errorMessage: string | null;
  cached: boolean;
  onRetry: () => void;
}

const numberFormat = new Intl.NumberFormat('id');

const PostImage: React.FC<{ post: Post }> = ({ post }) => {
  const [failed, setFailed] = useState(false);
  if (!post.imageUrl || failed) {
    // CDN links are signed and expire, so a cached feed can outlive its covers.
    return (
      <div className="w-full h-64 bg-gray-200 flex items-center justify-center px-4 text-center text-sm text-gray-700">
        {post.imageUrl ? 'Cover gagal dimuat. Klik "Scrape ulang" untuk link baru.' : 'Tidak ada cover'}
      </div>
    );
  }
  return (
    <img
      src={post.imageUrl}
      alt={post.caption || 'Post tanpa caption'}
      loading="lazy"
      onError={() => setFailed(true)}
      className="w-full h-64 object-cover bg-gray-200"
    />
  );
};

const Feed: React.FC<FeedProps> = ({
  status,
  posts,
  platform,
  username,
  errorMessage,
  cached,
  onRetry,
}) => {
  const platformLabel = platform === 'instagram' ? 'Instagram' : 'TikTok';
  const { user } = useAuth();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // post id -> folders that already hold it. Those posts are shown greyed out and cannot be picked.
  const [savedIn, setSavedIn] = useState<Record<string, string[]>>({});

  // A new feed means new post ids, so an old selection would point at nothing.
  useEffect(() => {
    setSelected(new Set());
  }, [posts]);

  const refreshSaved = useCallback(() => {
    if (!user || posts.length === 0) {
      setSavedIn({});
      return;
    }
    lookupDownloaded(
      platform,
      posts.map((post) => post.id),
    )
      .then(setSavedIn)
      .catch((error: unknown) => {
        // Not fatal: the feed still works, posts just are not marked as saved.
        console.error('lookupDownloaded failed', error);
      });
  }, [user, platform, posts]);

  useEffect(refreshSaved, [refreshSaved]);

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
        <button
          type="button"
          onClick={onRetry}
          className="min-h-[44px] px-4 rounded-md bg-gray-900 text-white text-sm font-semibold hover:bg-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-gray-900"
        >
          Coba lagi
        </button>
      </div>
    );
  }

  const stories = posts.filter((post) => post.isStory);
  const regularPosts = posts.filter((post) => !post.isStory);

  if (posts.length === 0) {
    return (
      <p className="bg-white rounded-lg shadow-md p-6 text-gray-700">
        @{username} belum punya post yang bisa diambil dalam rentang waktu ini.
      </p>
    );
  }

  const selectableIds = posts.filter((post) => !savedIn[post.id]).map((post) => post.id);
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));

  return (
    <div className={user ? 'pb-48 md:pb-32' : ''}>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <p className="text-sm text-gray-700">
          {platformLabel} @{username}, {posts.length} item
          {cached ? ' (dari cache)' : ''}
        </p>
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

      {stories.length > 0 && (
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4">Stories</h2>
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
        </div>
      )}

      <h2 className="text-2xl font-bold mb-4">Feed</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
        {regularPosts.map((post) => {
          const savedFolders = savedIn[post.id];
          return (
            <article
              key={post.id}
              className={`relative bg-white rounded-lg shadow-md overflow-hidden flex flex-col ${
                selected.has(post.id) ? 'ring-4 ring-gray-900' : ''
              } ${savedFolders ? '[&_img]:grayscale [&_img]:opacity-60' : ''}`}
            >
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
                <span className="absolute top-2 right-2 z-10 max-w-[70%] truncate rounded bg-gray-900 px-2 py-1 text-xs font-semibold text-white">
                  Di Library: {savedFolders.join(', ')}
                </span>
              )}
              {post.permalink ? (
                <a
                  href={post.permalink}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Buka post di ${platformLabel}`}
                  className="focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-gray-900"
                >
                  <PostImage post={post} />
                </a>
              ) : (
                <PostImage post={post} />
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
              <div className="bg-gray-100 px-4 py-2 text-sm text-gray-700">
                {platformLabel} • <time dateTime={post.createdAt}>{formatRelativeTime(post.createdAt)}</time>
              </div>
            </article>
          );
        })}
      </div>

      {user && (
        <DownloadBar
          platform={platform}
          username={username}
          selectedIds={posts.filter((post) => selected.has(post.id)).map((post) => post.id)}
          onClearSelection={() => setSelected(new Set())}
          onJobFinished={refreshSaved}
        />
      )}
    </div>
  );
};

export default Feed;
