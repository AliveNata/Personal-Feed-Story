import { useCallback, useEffect, useRef, useState } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import Feed, { FeedStatus } from './components/Feed';
import FeedControls from './components/FeedControls';
import FolderPage from './components/FolderPage';
import Header from './components/Header';
import LibraryPage from './components/LibraryPage';
import Login from './components/Login';
import RequireAuth from './components/RequireAuth';
import { Platform, Post } from './types';
import { ApiError, fetchFeed } from './utils/api';

interface FeedState {
  status: FeedStatus;
  posts: Post[];
  cached: boolean;
  errorMessage: string | null;
}

const IDLE_FEED: FeedState = { status: 'idle', posts: [], cached: false, errorMessage: null };

function App() {
  const [username, setUsername] = useState('');
  const [platform, setPlatform] = useState<Platform>('tiktok');
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
        setFeed({ status: 'success', posts: data.posts, cached: data.cached, errorMessage: null });
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        console.error('fetchFeed failed', error);
        const message =
          error instanceof ApiError
            ? error.message
            : 'Backend tidak bisa dihubungi. Pastikan service backend sudah jalan.';
        setFeed({ status: 'error', posts: [], cached: false, errorMessage: message });
      }
    },
    [],
  );

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
                    <FeedControls
                      platform={platform}
                      username={username}
                      isLoading={feed.status === 'loading'}
                      onSubmit={handleSearch}
                      onRefresh={() => void loadFeed(platform, username, true)}
                    />
                    <Feed
                      status={feed.status}
                      posts={feed.posts}
                      platform={platform}
                      username={username}
                      errorMessage={feed.errorMessage}
                      cached={feed.cached}
                      onRetry={() => void loadFeed(platform, username)}
                    />
                  </>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </Router>
    </AuthProvider>
  );
}

export default App;
