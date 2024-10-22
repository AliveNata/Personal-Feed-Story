import React, { useState } from 'react';

interface DashboardProps {
  setUsername: (username: string) => void;
  setPlatform: (platform: 'instagram' | 'tiktok') => void;
}

const Dashboard: React.FC<DashboardProps> = ({ setUsername, setPlatform }) => {
  const [newUsername, setNewUsername] = useState('');
  const [newPlatform, setNewPlatform] = useState<'instagram' | 'tiktok'>('instagram');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setUsername(newUsername);
    setPlatform(newPlatform);
    alert(`Added new feed: ${newUsername} on ${newPlatform}`);
    setNewUsername('');
  };

  return (
    <div className="max-w-md mx-auto bg-white p-8 rounded-lg shadow-md">
      <h2 className="text-2xl font-bold mb-6 text-center">Dashboard</h2>
      <form onSubmit={handleSubmit}>
        <div className="mb-4">
          <label htmlFor="newUsername" className="block text-gray-700 font-bold mb-2">
            New Username
          </label>
          <input
            type="text"
            id="newUsername"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            required
          />
        </div>
        <div className="mb-6">
          <label htmlFor="newPlatform" className="block text-gray-700 font-bold mb-2">
            Platform
          </label>
          <select
            id="newPlatform"
            value={newPlatform}
            onChange={(e) => setNewPlatform(e.target.value as 'instagram' | 'tiktok')}
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="instagram">Instagram</option>
            <option value="tiktok">TikTok</option>
          </select>
        </div>
        <button
          type="submit"
          className="w-full bg-green-500 text-white font-bold py-2 px-4 rounded-md hover:bg-green-600 focus:outline-none focus:ring-2 focus:ring-green-500"
        >
          Add New Feed
        </button>
      </form>
    </div>
  );
};

export default Dashboard;