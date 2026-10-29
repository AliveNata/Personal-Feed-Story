import { useCallback, useEffect, useRef, useState } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import FavoritesBar from './favorites/FavoritesBar';
import { FavoritesProvider } from './favorites/FavoritesContext';
import Feed, { FeedStatus } from './components/Feed';
import FeedControls from './components/FeedControls';
import FolderPage from './components/FolderPage';
import Header from './components/Header';
import LibraryPage from './components/LibraryPage';
import Login from './components/Login';
import RequireAuth from './components/RequireAuth';
import { Platform, Post, RateInfo, StoriesStatus } from './types';
import { ApiError, fetchFeed } from './utils/api';

interface FeedState {
  status: FeedStatus;
  posts: Post[];
  cached: boolean;
  errorMessage: string | null;
  nextCursor: string | null;
  storiesStatus: StoriesStatus;
  rate: RateInfo | null;
  isLoadingMore: boolean;
  loadMoreError: string | null;
}

const IDLE_FEED: FeedState = {
  status: 'idle',
  posts: [],
  cached: false,
  errorMessage: null,
  nextCursor: null,
  storiesStatus: 'none',
  rate: null,
  isLoadingMore: false,
  loadMoreError: null,
};

const describeError = (error: unknown): string =>
  error instanceof ApiError
    ? error.message
    : 'Backend tidak bisa dihubungi. Pastikan service backend sudah jalan.';

function App() {
  const [username, setUsername] = useState('');
  const [platform, setPlatform] = useState<Platform>('tiktok');
  // Toggle state of the search form, which can differ from the feed on screen until submit.
  const [pickedPlatform, setPickedPlatform] = useState<Platform>('tiktok');
  const [feed, setFeed] = useState<FeedState>(IDLE_FEED);
  const activeRequest = useRef<AbortController | null>(null);

  const loadFeed = useCallback(
    async (targetPlatform: Platform, targetUsername: string, refresh = false) => {
      if (!targetUsername) {
        setFeed(IDLE_FEED);
        return;
      }
      // A newer search wins, so drop whatever is still in flight.
      activeRequest.current?.abort();
      const controller = new AbortController();
      activeRequest.current = controller;

      setFeed((previous) => ({ ...previous, status: 'loading', errorMessage: null }));
      try {
        const data = await fetchFeed(targetPlatform, targetUsername, {
          refresh,
          signal: controller.signal,
        });
        setFeed({
          ...IDLE_FEED,
          status: 'success',
          posts: data.posts,
          cached: data.cached,
          nextCursor: data.nextCursor,
          storiesStatus: data.storiesStatus,
          rate: data.rate,
        });
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        console.error('fetchFeed failed', error);
        setFeed({ ...IDLE_FEED, status: 'error', errorMessage: describeError(error) });
      }
    },
    [],
  );

  const loadMore = useCallback(async () => {
    const cursor = feed.nextCursor;
    if (!cursor || feed.isLoadingMore) return;
    setFeed((previous) => ({ ...previous, isLoadingMore: true, loadMoreError: null }));
    try {
      const data = await fetchFeed(platform, username, { cursor });
      setFeed((previous) => {
        // Skip ids already shown: TikTok windows can overlap at the edges.
        const seen = new Set(previous.posts.map((post) => post.id));
        return {
          ...previous,
          posts: [...previous.posts, ...data.posts.filter((post) => !seen.has(post.id))],
          nextCursor: data.nextCursor,
          rate: data.rate,
          isLoadingMore: false,
        };
      });
    } catch (error) {
      console.error('load more failed', error);
      setFeed((previous) => ({
        ...previous,
        isLoadingMore: false,
        loadMoreError: describeError(error),
      }));
    }
  }, [feed.nextCursor, feed.isLoadingMore, platform, username]);

  useEffect(() => setPickedPlatform(platform), [platform]);

  useEffect(() => {
    void loadFeed(platform, username);
  }, [platform, username, loadFeed]);

  const handleSearch = (nextPlatform: Platform, nextUsername: string) => {
    if (nextPlatform === platform && nextUsername === username) {
      // Same feed again: state would not change, so trigger the fetch directly.
      void loadFeed(platform, username);
      return;
    }
    setPlatform(nextPlatform);
    setUsername(nextUsername);
  };

  return (
    <AuthProvider>
      <FavoritesProvider>
        <Router>
          <div className="min-h-screen bg-gradient-to-br from-purple-100 to-pink-100">
            <Header platform={platform} />
            <main className="container mx-auto px-4 py-8">
              <Routes>
                <Route path="/login" element={<Login />} />
                <Route
                  path="/library"
                  element={
                    <RequireAuth>
                      <LibraryPage />
                    </RequireAuth>
                  }
                />
                <Route
                  path="/library/:folderId"
                  element={
                    <RequireAuth>
                      <FolderPage />
                    </RequireAuth>
                  }
                />
                <Route
                  path="/"
                  element={
                    <>
                      <FavoritesBar
                        activePlatform={platform}
                        shownPlatform={pickedPlatform}
                        activeUsername={username}
                        onOpen={handleSearch}
                      />
                      <FeedControls
                        platform={platform}
                        username={username}
                        isLoading={feed.status === 'loading'}
                        onSubmit={handleSearch}
                        onRefresh={() => void loadFeed(platform, username, true)}
                        onPlatformChange={setPickedPlatform}
                      />
                      <Feed
                        status={feed.status}
                        posts={feed.posts}
                        platform={platform}
                        username={username}
                        errorMessage={feed.errorMessage}
                        cached={feed.cached}
                        storiesStatus={feed.storiesStatus}
                        rate={feed.rate}
                        hasMore={feed.nextCursor !== null}
                        isLoadingMore={feed.isLoadingMore}
                        loadMoreError={feed.loadMoreError}
                        onRetry={() => void loadFeed(platform, username)}
                        onLoadMore={() => void loadMore()}
                      />
                    </>
                  }
                />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </main>
          </div>
        </Router>
      </FavoritesProvider>
    </AuthProvider>
  );
}

export default App;
