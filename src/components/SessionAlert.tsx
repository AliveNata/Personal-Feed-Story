import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Platform, PlatformSessionStatus } from '../types';
import { fetchPlatformSessions } from '../utils/api';
import { focusRing } from './ui';

// Often enough to notice a dead session soon after it happens, cheap: no platform request.
const POLL_MS = 2 * 60 * 1000;
const LABELS: Record<Platform, string> = { instagram: 'Instagram', tiktok: 'TikTok' };

/** Banner on every page when a platform cookie should be replaced, linking to the Session page. */
const SessionAlert: React.FC = () => {
  const { user } = useAuth();
  const location = useLocation();
  const [needsReplace, setNeedsReplace] = useState<PlatformSessionStatus[]>([]);

  useEffect(() => {
    if (!user) {
      setNeedsReplace([]);
      return;
    }
    const load = () =>
      fetchPlatformSessions()
        .then((sessions) => setNeedsReplace(sessions.filter((session) => session.replaceReason)))
        .catch((error: unknown) => console.error('fetchPlatformSessions failed', error));
    load();
    // A failed scrape is often what flags the session: re-check on every page change too.
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [user, location.pathname, location.search]);

  // The Session page shows the same reason inside its cards.
  if (needsReplace.length === 0 || location.pathname === '/session') return null;

  return (
    <div role="alert" className="border-b border-amber-400 bg-amber-50">
      <div className="container mx-auto flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm text-amber-900">
        <p className="min-w-0 flex-1">
          {needsReplace.map((session) => (
            <span key={session.platform} className="block">
              <span className="font-semibold">Cookie {LABELS[session.platform]} perlu diganti:</span>{' '}
              {session.replaceReason}.
            </span>
          ))}
        </p>
        <Link
          to="/session"
          className={`inline-flex min-h-[44px] items-center rounded-md bg-amber-900 px-4 font-semibold text-white hover:bg-amber-800 ${focusRing}`}
        >
          Ganti cookie
        </Link>
      </div>
    </div>
  );
};

export default SessionAlert;
