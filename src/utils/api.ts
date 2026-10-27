import { FeedResponse, Platform, Post, User } from '../types';

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const fetchFeed = async (
  platform: Platform,
  username: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<FeedResponse> => {
  const params = new URLSearchParams({ platform, username });
  if (options.refresh) {
    params.set('refresh', 'true');
  }

  const response = await fetch(`/api/feed?${params.toString()}`, { signal: options.signal });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
        ? body.detail
        : // Vite's proxy answers an empty 500 when the backend is down, so cover both cases.
          response.status === 500
          ? 'Backend mati atau error. Cek terminal docker compose up.'
          : `Request gagal (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as FeedResponse;
};

const relativeTimeFormat = new Intl.RelativeTimeFormat('id', { numeric: 'auto' });
const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
];

export const formatRelativeTime = (isoDate: string, now: Date = new Date()): string => {
  const diffSeconds = (new Date(isoDate).getTime() - now.getTime()) / 1000;
  for (const [unit, seconds] of RELATIVE_UNITS) {
    if (Math.abs(diffSeconds) >= seconds) {
      return relativeTimeFormat.format(Math.round(diffSeconds / seconds), unit);
    }
  }
  return relativeTimeFormat.format(0, 'minute');
};

// Login and Google Drive are still mocked: out of scope for the scraper backend.
const mockUser: User = {
  email: 'user@example.com',
};

export const fetchUser = async (): Promise<User> => {
  await new Promise((resolve) => setTimeout(resolve, 300));
  return mockUser;
};

export const saveStoryToGDrive = async (story: Post, email: string): Promise<boolean> => {
  await new Promise((resolve) => setTimeout(resolve, 1000));
  console.log(`Saving story ${story.id} to Google Drive for ${email}`);
  return true;
};
