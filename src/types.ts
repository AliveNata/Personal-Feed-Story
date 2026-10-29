export type Platform = 'instagram' | 'tiktok';

export interface MediaSlide {
  type: 'photo' | 'video';
  url: string;
  posterUrl: string | null;
}

export type StoriesStatus = 'available' | 'none' | 'login_required' | 'not_supported';

export interface RateInfo {
  used: number;
  limit: number;
  windowSeconds: number;
  waitSeconds: number;
  nearLimit: boolean;
}

export interface Post {
  id: string;
  imageUrl: string;
  caption: string;
  likes: number;
  comments: number;
  // Instagram does not expose share counts.
  shares: number | null;
  createdAt: string;
  isStory: boolean;
  permalink: string | null;
  media: MediaSlide[];
}

export interface FeedResponse {
  platform: Platform;
  username: string;
  posts: Post[];
  cached: boolean;
  nextCursor: string | null;
  storiesStatus: StoriesStatus;
  rate: RateInfo | null;
}

export interface Highlight {
  id: string;
  title: string;
  coverUrl: string;
  fallbackCoverUrl: string | null;
  itemCount: number | null;
}

export interface HighlightsResponse {
  status: 'available' | 'none' | 'login_required';
  highlights: Highlight[];
  rate: RateInfo | null;
}

export interface HighlightItemsResponse {
  items: Post[];
  rate: RateInfo | null;
}

export interface User {
  username: string;
}

export type MediaType = 'photo' | 'video';
export type JobStatus = 'queued' | 'running' | 'done' | 'failed';

export interface DownloadJob {
  id: string;
  folderId: number;
  status: JobStatus;
  totalPosts: number;
  donePosts: number;
  failedPosts: number;
  savedItems: number;
  skippedItems: number;
  errors: { postId: string; message: string }[];
}

export type MediaSource = 'post' | 'story' | 'upload';

export interface Folder {
  id: number;
  name: string;
  photoCount: number;
  videoCount: number;
  sizeBytes: number;
  createdAt: string;
  counts: Record<MediaSource, Record<MediaType, number>>;
}

export interface MediaItem {
  id: number;
  mediaType: MediaType;
  platform: Platform | 'upload';
  authorUsername: string;
  sourcePostId: string;
  itemIndex: number;
  isStory: boolean;
  caption: string;
  permalink: string | null;
  postedAt: string | null;
  sizeBytes: number;
  fileUrl: string;
  thumbnailUrl: string | null;
}

export type FavoriteUpdate = 'post' | 'story';

export interface Favorite {
  id: number;
  platform: Platform;
  username: string;
  avatarUrl: string | null;
  updates: FavoriteUpdate[];
  checkedAt: string | null;
  checkError: string | null;
}

export interface FavoritesResponse {
  favorites: Favorite[];
  checking: boolean;
}
