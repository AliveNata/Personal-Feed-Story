import React, { useEffect, useState } from 'react';
import { Platform } from '../types';

interface FeedControlsProps {
  platform: Platform;
  username: string;
  isLoading: boolean;
  onSubmit: (platform: Platform, username: string) => void;
  onRefresh: () => void;
  // The toggle also filters the favorites row, before any search is sent.
  onPlatformChange: (platform: Platform) => void;
}

const PLATFORM_LABELS: Record<Platform, string> = {
  instagram: 'Instagram',
  tiktok: 'TikTok',
};

const FeedControls: React.FC<FeedControlsProps> = ({
  platform,
  username,
  isLoading,
  onSubmit,
  onRefresh,
  onPlatformChange,
}) => {
  const [draftPlatform, setDraftPlatform] = useState<Platform>(platform);
  const [draftUsername, setDraftUsername] = useState(username);

  // Dashboard can change the active feed too, so keep the form in sync with it.
  useEffect(() => {
    setDraftPlatform(platform);
    setDraftUsername(username);
  }, [platform, username]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const cleaned = draftUsername.trim().replace(/^@/, '');
    if (cleaned) {
      onSubmit(draftPlatform, cleaned);
    }
  };

  const focusRing =
    'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-gray-900';

  return (
    <form
      onSubmit={handleSubmit}
      className="mb-8 bg-white rounded-lg shadow-md p-4 flex flex-col gap-3 md:flex-row md:items-end"
    >
      <fieldset className="flex-shrink-0">
        <legend className="block text-sm font-semibold text-gray-700 mb-1">Platform</legend>
        <div className="flex rounded-md border border-gray-300 overflow-hidden">
          {(Object.keys(PLATFORM_LABELS) as Platform[]).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={draftPlatform === option}
              onClick={() => {
                setDraftPlatform(option);
                onPlatformChange(option);
              }}
              className={`flex-1 md:flex-none min-h-[44px] px-4 text-sm font-medium ${focusRing} ${
                draftPlatform === option
                  ? 'bg-gray-900 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-100'
              }`}
            >
              {PLATFORM_LABELS[option]}
            </button>
          ))}
        </div>
      </fieldset>

      <div className="flex-1 min-w-0">
        <label htmlFor="feed-username" className="block text-sm font-semibold text-gray-700 mb-1">
          Username
        </label>
        <input
          id="feed-username"
          type="text"
          value={draftUsername}
          onChange={(event) => setDraftUsername(event.target.value)}
          placeholder="contoh: natgeo"
          autoComplete="off"
          spellCheck={false}
          pattern="@?[A-Za-z0-9._\-]{1,100}"
          title="Huruf, angka, titik, underscore, atau strip"
          required
          className={`w-full min-h-[44px] px-3 border border-gray-300 rounded-md text-gray-900 ${focusRing}`}
        />
      </div>

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={isLoading}
          className={`flex-1 md:flex-none min-h-[44px] px-4 rounded-md bg-gray-900 text-white text-sm font-semibold hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed ${focusRing}`}
        >
          {isLoading ? 'Mengambil...' : 'Ambil feed'}
        </button>
        <button
          type="button"
          onClick={onRefresh}
          disabled={isLoading || !username}
          title="Abaikan cache dan scrape ulang dari platform"
          className={`flex-1 md:flex-none min-h-[44px] px-4 rounded-md border border-gray-300 bg-white text-gray-800 text-sm font-medium hover:bg-gray-100 disabled:opacity-60 disabled:cursor-not-allowed ${focusRing}`}
        >
          Scrape ulang
        </button>
      </div>
    </form>
  );
};

export default FeedControls;
