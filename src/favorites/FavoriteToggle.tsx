import React, { useState } from 'react';
import { Star } from 'lucide-react';
import { secondaryButton } from '../components/ui';
import { Platform } from '../types';
import { ApiError } from '../utils/api';
import { useFavorites } from './FavoritesContext';

interface FavoriteToggleProps {
  platform: Platform;
  username: string;
}

const FavoriteToggle: React.FC<FavoriteToggleProps> = ({ platform, username }) => {
  const { status, find, add, remove } = useFavorites();
  const [isWorking, setIsWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const favorite = find(platform, username);

  const handleClick = async () => {
    setIsWorking(true);
    setError(null);
    try {
      if (favorite) {
        await remove(favorite.id);
      } else {
        await add(platform, username);
      }
    } catch (caught) {
      console.error('favorite toggle failed', caught);
      setError(caught instanceof ApiError ? caught.message : 'Gagal menyimpan favorit');
    } finally {
      setIsWorking(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={isWorking || status !== 'ready'}
        aria-pressed={favorite !== undefined}
        className={`inline-flex items-center gap-2 ${secondaryButton}`}
      >
        <Star
          aria-hidden="true"
          className={`h-4 w-4 ${favorite ? 'fill-gray-900 text-gray-900' : 'text-gray-700'}`}
        />
        {isWorking ? 'Menyimpan...' : favorite ? 'Hapus favorit' : 'Favoritkan'}
      </button>
      {error && (
        <span role="alert" className="text-sm text-red-700">
          {error}
        </span>
      )}
    </>
  );
};

export default FavoriteToggle;
