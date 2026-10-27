import {
  DownloadJob,
  FeedResponse,
  Folder,
  MediaItem,
  MediaType,
  Platform,
  User,
} from '../types';

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const errorDetail = async (response: Response): Promise<string> => {
  const body: unknown = await response.json().catch(() => null);
  if (body && typeof body === 'object' && 'detail' in body) {
    const { detail } = body as { detail: unknown };
    if (typeof detail === 'string') {
      return detail;
    }
    // FastAPI validation errors come back as a list of {msg} objects.
    if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === 'string') {
      return detail[0].msg;
    }
  }
  // Vite's proxy answers an empty 500 when the backend is down, so cover both cases.
  return response.status === 500
    ? 'Backend mati atau error. Cek terminal docker compose up.'
    : `Request gagal (${response.status})`;
};

const request = async <T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> => {
  const { json, ...rest } = init;
  const response = await fetch(path, {
    ...rest,
    headers: json === undefined ? rest.headers : { 'Content-Type': 'application/json' },
    body: json === undefined ? rest.body : JSON.stringify(json),
    credentials: 'same-origin',
  });
  if (!response.ok) {
    throw new ApiError(await errorDetail(response), response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
};

export const fetchFeed = (
  platform: Platform,
  username: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<FeedResponse> => {
  const params = new URLSearchParams({ platform, username });
  if (options.refresh) {
    params.set('refresh', 'true');
  }
  return request<FeedResponse>(`/api/feed?${params.toString()}`, { signal: options.signal });
};

export const fetchMe = async (): Promise<User | null> =>
  (await request<{ user: User | null }>('/api/auth/me')).user;

export const login = (username: string, password: string): Promise<User> =>
  request<User>('/api/auth/login', { method: 'POST', json: { username, password } });

export const logout = (): Promise<void> => request<void>('/api/auth/logout', { method: 'POST' });

export const createDownload = (body: {
  folderName: string;
  platform: Platform;
  username: string;
  postIds: string[];
}): Promise<DownloadJob> => request<DownloadJob>('/api/downloads', { method: 'POST', json: body });

export const fetchJob = (jobId: string): Promise<DownloadJob> =>
  request<DownloadJob>(`/api/jobs/${encodeURIComponent(jobId)}`);

export const fetchFolders = (): Promise<Folder[]> => request<Folder[]>('/api/folders');

export const fetchFolder = (folderId: number): Promise<Folder> =>
  request<Folder>(`/api/folders/${folderId}`);

export const fetchFolderItems = (folderId: number, type: MediaType): Promise<MediaItem[]> =>
  request<MediaItem[]>(`/api/folders/${folderId}/items?type=${type}`);

export const deleteFolder = (folderId: number): Promise<void> =>
  request<void>(`/api/folders/${folderId}`, { method: 'DELETE' });

export const deleteItem = (itemId: number): Promise<void> =>
  request<void>(`/api/items/${itemId}`, { method: 'DELETE' });

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

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export const formatBytes = (bytes: number): string => {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toLocaleString('id', { maximumFractionDigits: unit === 0 ? 0 : 1 })} ${BYTE_UNITS[unit]}`;
};
