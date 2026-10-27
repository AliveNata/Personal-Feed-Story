import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { User } from '../types';
import { fetchMe, login as apiLogin, logout as apiLogout } from '../utils/api';

interface AuthState {
  user: User | null;
  isChecking: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isChecking, setIsChecking] = useState(true);

  useEffect(() => {
    fetchMe()
      .then(setUser)
      .catch((error: unknown) => {
        // Backend down: treat as logged out, the feed page shows its own error.
        console.error('session check failed', error);
        setUser(null);
      })
      .finally(() => setIsChecking(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setUser(await apiLogin(username, password));
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isChecking, login, logout }}>{children}</AuthContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = (): AuthState => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return context;
};
