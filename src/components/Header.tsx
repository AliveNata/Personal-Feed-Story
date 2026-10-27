import React, { useState } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { Instagram, LogOut } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { Platform } from '../types';
import TikTokIcon from './TikTokIcon';
import { focusRing } from './ui';

interface HeaderProps {
  platform: Platform;
}

const navClass = ({ isActive }: { isActive: boolean }) =>
  `inline-flex min-h-[44px] items-center px-3 rounded-md text-sm font-semibold ${focusRing} ${
    isActive ? 'bg-gray-900 text-white' : 'text-gray-800 hover:bg-gray-100'
  }`;

const Header: React.FC<HeaderProps> = ({ platform }) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [logoutError, setLogoutError] = useState<string | null>(null);

  const handleLogout = async () => {
    try {
      await logout();
      navigate('/');
    } catch (error) {
      console.error('logout failed', error);
      setLogoutError('Logout gagal, coba lagi');
    }
  };

  return (
    <header className="bg-white shadow-md">
      <div className="container mx-auto px-4 py-3 flex flex-wrap items-center gap-3">
        <Link to="/" className={`flex items-center rounded-md ${focusRing}`}>
          {platform === 'instagram' ? (
            <Instagram className="w-8 h-8 text-pink-500 mr-2" aria-hidden="true" />
          ) : (
            <TikTokIcon className="w-8 h-8 text-black mr-2" />
          )}
          <span className="text-2xl font-bold text-gray-800">Social Feed</span>
        </Link>
        <nav aria-label="Menu utama" className="ml-auto flex flex-wrap items-center gap-1">
          <NavLink to="/" end className={navClass}>
            Feed
          </NavLink>
          {user && (
            <NavLink to="/library" className={navClass}>
              Library
            </NavLink>
          )}
          {user ? (
            <button
              type="button"
              onClick={handleLogout}
              className={`inline-flex min-h-[44px] items-center px-3 rounded-md text-sm font-semibold text-gray-800 hover:bg-gray-100 ${focusRing}`}
            >
              <LogOut className="w-4 h-4 mr-2" aria-hidden="true" />
              Logout ({user.username})
            </button>
          ) : (
            <NavLink to="/login" className={navClass}>
              Login
            </NavLink>
          )}
        </nav>
        {logoutError && (
          <p role="alert" className="w-full text-right text-sm text-red-700">
            {logoutError}
          </p>
        )}
      </div>
    </header>
  );
};

export default Header;
