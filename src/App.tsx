import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import Login from './components/Login';
import Dashboard from './components/Dashboard';
import Feed from './components/Feed';
import Header from './components/Header';
import { fetchMockData, fetchUser } from './utils/api';
import { Post, User as UserType } from './types';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState('johndoe');
  const [platform, setPlatform] = useState<'instagram' | 'tiktok'>('instagram');
  const [posts, setPosts] = useState<Post[]>([]);
  const [user, setUser] = useState<UserType | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      const data = await fetchMockData(platform, username);
      setPosts(data);
    };
    fetchData();
  }, [platform, username]);

  useEffect(() => {
    const getUser = async () => {
      const userData = await fetchUser();
      setUser(userData);
    };
    getUser();
  }, []);

  const handleLogin = (success: boolean) => {
    setIsAuthenticated(success);
  };

  if (!user) {
    return <div>Loading...</div>;
  }

  return (
    <Router>
      <div className="min-h-screen bg-gradient-to-br from-purple-100 to-pink-100">
        <Header
          username={username}
          setUsername={setUsername}
          platform={platform}
          setPlatform={setPlatform}
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
              element={<Feed posts={posts} platform={platform} user={user} />}
            />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;