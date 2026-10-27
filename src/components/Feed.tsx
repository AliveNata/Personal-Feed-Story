import React, { useState } from 'react';
import { Heart, MessageCircle, Share2 } from 'lucide-react';
import { Platform, Post, User } from '../types';
import { formatRelativeTime } from '../utils/api';
import Story from './Story';

export type FeedStatus = 'idle' | 'loading' | 'success' | 'error';

interface FeedProps {
  status: FeedStatus;
  posts: Post[];
  platform: Platform;
  username: string;
  errorMessage: string | null;
  cached: boolean;
  user: User;
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
  user,
  onRetry,
}) => {
  const platformLabel = platform === 'instagram' ? 'Instagram' : 'TikTok';

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

  return (
    <div>
      <p className="text-sm text-gray-700 mb-4">
        {platformLabel} @{username}, {posts.length} item
        {cached ? ' (dari cache)' : ''}
      </p>

      {stories.length > 0 && (
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4">Stories</h2>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-4">
            {stories.map((story) => (
              <Story key={story.id} story={story} user={user} />
            ))}
          </div>
        </div>
      )}

      <h2 className="text-2xl font-bold mb-4">Feed</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
        {regularPosts.map((post) => (
          <article key={post.id} className="bg-white rounded-lg shadow-md overflow-hidden flex flex-col">
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
        ))}
      </div>
    </div>
  );
};

export default Feed;
