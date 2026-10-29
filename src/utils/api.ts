import {
  DownloadJob,
  Favorite,
  FavoritesResponse,
  FeedResponse,
  FreshMediaResponse,
  Folder,
  HighlightItemsResponse,
  HighlightsResponse,
  ReelsResponse,
  MediaItem,
  MediaSource,
  MediaType,
  Platform,
  User,
} from '../types';

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    // Seconds the backend asks us to wait, set on 429 rate limit answers.
    public readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const toApiError = async (response: Response): Promise<ApiError> => {
  const body: unknown = await response.json().catch(() => null);
  const retryAfter =
    body && typeof body === 'object' && 'retryAfter' in body && typeof body.retryAfter === 'number'
      ? body.retryAfter
      : null;
  return new ApiError(errorDetail(body, response.status), response.status, retryAfter);
};

const errorDetail = (body: unknown, status: number): string => {
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
  return status === 500
    ? 'Backend mati atau error. Cek terminal docker compose up.'
    : `Request gagal (${status})`;
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
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
};

export const fetchFeed = (
  platform: Platform,
  username: string,
  options: { refresh?: boolean; cursor?: string | null; signal?: AbortSignal } = {},
): Promise<FeedResponse> => {
  const params = new URLSearchParams({ platform, username });
  if (options.refresh) {
    params.set('refresh', 'true');
  }
  if (options.cursor) {
    params.set('cursor', options.cursor);
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

export const fetchFolderItems = (
  folderId: number,
  type: MediaType,
  source: MediaSource,
): Promise<MediaItem[]> =>
  request<MediaItem[]>(`/api/folders/${folderId}/items?type=${type}&source=${source}`);

export const fetchHighlights = (username: string): Promise<HighlightsResponse> =>
  request<HighlightsResponse>(
    `/api/instagram/highlights?username=${encodeURIComponent(username)}`,
  );

export const fetchReels = (username: string, cursor: string | null): Promise<ReelsResponse> => {
  const params = new URLSearchParams({ username });
  if (cursor) params.set('cursor', cursor);
  return request<ReelsResponse>(`/api/instagram/reels?${params.toString()}`);
};

export const fetchHighlightItems = (
  username: string,
  highlightId: string,
): Promise<HighlightItemsResponse> =>
  request<HighlightItemsResponse>(
    `/api/instagram/highlights/${encodeURIComponent(highlightId)}?username=${encodeURIComponent(username)}`,
  );

export const deleteFolder = (folderId: number): Promise<void> =>
  request<void>(`/api/folders/${folderId}`, { method: 'DELETE' });

export interface UploadResult {
  status: 'saved' | 'skipped';
  item: MediaItem | null;
}

/** XHR instead of fetch: fetch has no upload progress events. */
export const uploadFile = (
  folderId: number,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<UploadResult> =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/folders/${folderId}/files`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch (error) {
        console.error('upload response was not JSON', error);
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as UploadResult);
        return;
      }
      const detail =
        body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
          ? body.detail
          : xhr.status === 413
            ? 'File terlalu besar'
            : `Upload gagal (${xhr.status})`;
      reject(new ApiError(detail, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('Koneksi ke backend putus', 0));
    const form = new FormData();
    form.append('file', file);
    xhr.send(form);
  });

export const lookupDownloaded = async (
  platform: Platform,
  postIds: string[],
): Promise<Record<string, string[]>> =>
  (
    await request<{ foldersByPost: Record<string, string[]> }>('/api/downloaded/lookup', {
      method: 'POST',
      json: { platform, postIds },
    })
  ).foldersByPost;

export const deleteItem = (itemId: number): Promise<void> =>
  request<void>(`/api/items/${itemId}`, { method: 'DELETE' });

export interface MoveResult {
  folderId: number;
  folderName: string;
  moved: number;
  // Already in the target folder, so the copy here was dropped instead of moved.
  merged: number;
}

export const moveItems = (itemIds: number[], folderName: string): Promise<MoveResult> =>
  request<MoveResult>('/api/items/move', { method: 'POST', json: { itemIds, folderName } });

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
  // Intl says "menit ini" for anything under a minute, which reads oddly in Indonesian.
  if (Math.abs(diffSeconds) < 60) return 'baru saja';
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

export const fetchFavorites = (): Promise<FavoritesResponse> =>
  request<FavoritesResponse>('/api/favorites');

export const addFavorite = (platform: Platform, username: string): Promise<Favorite> =>
  request<Favorite>('/api/favorites', { method: 'POST', json: { platform, username } });

export const deleteFavorite = (favoriteId: number): Promise<void> =>
  request<void>(`/api/favorites/${favoriteId}`, { method: 'DELETE' });

export const markFavoriteSeen = (favoriteId: number): Promise<Favorite> =>
  request<Favorite>(`/api/favorites/${favoriteId}/seen`, { method: 'POST' });

export const checkFavoritesNow = (): Promise<{ checking: boolean }> =>
  request<{ checking: boolean }>('/api/favorites/check', { method: 'POST' });

/** New links for a cached post whose video link expired. Costs a platform request. */
export const fetchFreshMedia = (
  platform: Platform,
  username: string,
  postId: string,
  expiredUrl: string,
): Promise<FreshMediaResponse> => {
  const params = new URLSearchParams({ platform, username, postId, expiredUrl });
  return request<FreshMediaResponse>(`/api/media/fresh?${params.toString()}`);
};
