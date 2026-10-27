import { useCallback, useEffect, useRef, useState } from 'react';
import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import Login from './components/Login';
import Dashboard from './components/Dashboard';
import Feed, { FeedStatus } from './components/Feed';
import FeedControls from './components/FeedControls';
import Header from './components/Header';
import { ApiError, fetchFeed, fetchUser } from './utils/api';
import { Platform, Post, User as UserType } from './types';

interface FeedState {
  status: FeedStatus;
  posts: Post[];
  cached: boolean;
  errorMessage: string | null;
}

const IDLE_FEED: FeedState = { status: 'idle', posts: [], cached: false, errorMessage: null };

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState('');
  const [platform, setPlatform] = useState<Platform>('tiktok');
  const [feed, setFeed] = useState<FeedState>(IDLE_FEED);
  const [user, setUser] = useState<UserType | null>(null);
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

  useEffect(() => {
    const getUser = async () => {
      const userData = await fetchUser();
      setUser(userData);
    };
    void getUser();
  }, []);

  const handleLogin = (success: boolean) => {
    setIsAuthenticated(success);
  };

  const handleSearch = (nextPlatform: Platform, nextUsername: string) => {
    if (nextPlatform === platform && nextUsername === username) {
      // Same feed again: state would not change, so trigger the fetch directly.
      void loadFeed(platform, username);
      return;
    }
    setPlatform(nextPlatform);
    setUsername(nextUsername);
  };

  if (!user) {
    return <div>Loading...</div>;
  }

  return (
    <Router>
      <div className="min-h-screen bg-gradient-to-br from-purple-100 to-pink-100">
        <Header
          platform={platform}
          isAuthenticated={isAuthenticated}
          setIsAuthenticated={setIsAuthenticated}
        />
        <main className="container mx-auto px-4 py-8">
          <Routes>
            <Route path="/login" element={<Login onLogin={handleLogin} />} />
            <Route
              path="/dashboard"
              element={
                isAuthenticated ? (
                  <Dashboard
                    setUsername={setUsername}
                    setPlatform={setPlatform}
                  />
                ) : (
                  <Navigate to="/login" />
                )
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
                    user={user}
                    onRetry={() => void loadFeed(platform, username)}
                  />
                </>
              }
            />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
