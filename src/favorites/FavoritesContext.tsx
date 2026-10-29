import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Favorite, Platform } from '../types';
import {
  ApiError,
  addFavorite,
  checkFavoritesNow,
  deleteFavorite,
  fetchFavorites,
  markFavoriteSeen,
} from '../utils/api';

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

interface FavoritesState {
  status: LoadStatus;
  favorites: Favorite[];
  errorMessage: string | null;
  isChecking: boolean;
  reload: () => void;
  find: (platform: Platform, username: string) => Favorite | undefined;
  add: (platform: Platform, username: string) => Promise<void>;
  remove: (favoriteId: number) => Promise<void>;
  markSeen: (platform: Platform, username: string) => void;
  checkNow: () => Promise<void>;
}

// How often the list is re-read while the worker is checking accounts.
const CHECK_POLL_MS = 5000;

const FavoritesContext = createContext<FavoritesState | null>(null);

const sameAccount = (favorite: Favorite, platform: Platform, username: string) =>
  favorite.platform === platform && favorite.username.toLowerCase() === username.toLowerCase();

const errorText = (error: unknown, fallback: string) =>
  error instanceof ApiError ? error.message : fallback;

export const FavoritesProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user } = useAuth();
  const [status, setStatus] = useState<LoadStatus>('idle');
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isChecking, setIsChecking] = useState(false);

  const load = useCallback((silent: boolean) => {
    if (!silent) setStatus('loading');
    fetchFavorites()
      .then((response) => {
        setFavorites(response.favorites);
        setIsChecking(response.checking);
        setStatus('ready');
        setErrorMessage(null);
      })
      .catch((error: unknown) => {
        console.error('fetchFavorites failed', error);
        // A failed background refresh keeps the list already on screen.
        if (!silent) {
          setStatus('error');
          setErrorMessage(errorText(error, 'Gagal memuat favorit'));
        }
      });
  }, []);

  useEffect(() => {
    if (user) {
      load(false);
    } else {
      setFavorites([]);
      setStatus('idle');
    }
  }, [user, load]);

  useEffect(() => {
    if (!isChecking) return;
    const timer = window.setInterval(() => load(true), CHECK_POLL_MS);
    return () => window.clearInterval(timer);
  }, [isChecking, load]);

  const replace = useCallback((updated: Favorite) => {
    setFavorites((previous) => {
      const exists = previous.some((favorite) => favorite.id === updated.id);
      return exists
        ? previous.map((favorite) => (favorite.id === updated.id ? updated : favorite))
        : [...previous, updated];
    });
  }, []);

  const find = useCallback(
    (platform: Platform, username: string) =>
      favorites.find((favorite) => sameAccount(favorite, platform, username)),
    [favorites],
  );

  const add = useCallback(
    async (platform: Platform, username: string) => {
      replace(await addFavorite(platform, username));
    },
    [replace],
  );

  const remove = useCallback(async (favoriteId: number) => {
    await deleteFavorite(favoriteId);
    setFavorites((previous) => previous.filter((favorite) => favorite.id !== favoriteId));
  }, []);

  const markSeen = useCallback(
    (platform: Platform, username: string) => {
      const favorite = favorites.find((item) => sameAccount(item, platform, username));
      if (!favorite || favorite.updates.length === 0) return;
      // Clear the ring right away, the request only makes it stick.
      replace({ ...favorite, updates: [] });
      markFavoriteSeen(favorite.id)
        .then(replace)
        .catch((error: unknown) => console.error('markFavoriteSeen failed', error));
    },
    [favorites, replace],
  );

  const checkNow = useCallback(async () => {
    await checkFavoritesNow();
    setIsChecking(true);
  }, []);

  const value = useMemo(
    () => ({
      status,
      favorites,
      errorMessage,
      isChecking,
      reload: () => load(false),
      find,
      add,
      remove,
      markSeen,
      checkNow,
    }),
    [status, favorites, errorMessage, isChecking, load, find, add, remove, markSeen, checkNow],
  );

  return <FavoritesContext.Provider value={value}>{children}</FavoritesContext.Provider>;
};

// eslint-disable-next-line react-refresh/only-export-components
export const useFavorites = (): FavoritesState => {
  const context = useContext(FavoritesContext);
  if (!context) {
    throw new Error('useFavorites must be used inside FavoritesProvider');
  }
  return context;
};
