import React from 'react';
import { Link } from 'react-router-dom';
import { Instagram, LogOut } from 'lucide-react';
import TikTokIcon from './TikTokIcon';

interface HeaderProps {
  username: string;
  setUsername: (username: string) => void;
  platform: 'instagram' | 'tiktok';
  setPlatform: (platform: 'instagram' | 'tiktok') => void;
  isAuthenticated: boolean;
  setIsAuthenticated: (isAuthenticated: boolean) => void;
}

const Header: React.FC<HeaderProps> = ({
  username,
  setUsername,
  platform,
  setPlatform,
  isAuthenticated,
  setIsAuthenticated,
}) => {
  const handleLogout = () => {
    setIsAuthenticated(false);
  };

  return (
    <header className="bg-white shadow-md">
      <div className="container mx-auto px-4 py-4 flex items-center justify-between">
        <div className="flex items-center">
          {platform === 'instagram' ? (
            <Instagram className="w-8 h-8 text-pink-500 mr-2" />
          ) : (
            <TikTokIcon className="w-8 h-8 text-black mr-2" />
          )}
          <Link to="/" className="text-2xl font-bold text-gray-800">
            Social Feed
          </Link>
        </div>
        <div className="flex items-center">
          {isAuthenticated ? (
            <>
              <Link
                to="/dashboard"
                className="px-4 py-2 bg-blue-500 text-white rounded-md hover:bg-blue-600 mr-4"
              >
                Dashboard
              </Link>
              <button
                onClick={handleLogout}
                className="flex items-center px-4 py-2 bg-red-500 text-white rounded-md hover:bg-red-600"
              >
                <LogOut className="w-5 h-5 mr-2" />
                Logout
              </button>
            </>
          ) : (
            <Link
              to="/login"
              className="px-4 py-2 bg-green-500 text-white rounded-md hover:bg-green-600"
            >
              Login
            </Link>
          )}
        </div>
      </div>
    </header>
  );
};

export default Header;