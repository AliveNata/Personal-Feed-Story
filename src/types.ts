export type Platform = 'instagram' | 'tiktok';

export interface MediaSlide {
  type: 'photo' | 'video';
  url: string;
  posterUrl: string | null;
}

// on_demand: Instagram stories load with their own request when asked.
export type StoriesStatus = 'available' | 'none' | 'login_required' | 'not_supported' | 'on_demand';

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
  // When the data was scraped, and when the account was last checked for changes.
  fetchedAt: string | null;
  checkedAt: string | null;
}

export interface FreshMediaResponse {
  urls: string[];
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

export interface ReelsResponse {
  status: 'available' | 'none' | 'login_required';
  reels: Post[];
  nextCursor: string | null;
  cached: boolean;
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
  // Live progress of the post being downloaded, null between posts or when unknown.
  currentPostId: string | null;
  currentPercent: number | null;
}

export type MediaSource = 'post' | 'story' | 'upload';

export interface Folder {
  id: number;
  name: string;
  // For the URL: /library/{slug}.
  slug: string;
  photoCount: number;
  videoCount: number;
  sizeBytes: number;
  createdAt: string;
  counts: Record<MediaSource, Record<MediaType, number>>;
  // Random thumbnails from the folder, no file twice. Empty for an empty folder.
  previewUrls: string[];
}

export type Rotation = 0 | 90 | 180 | 270;

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
  addedAt: string;
  // Clockwise display rotation. The file itself is not rotated.
  rotation: Rotation;
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
  newPostCount: number;
  checkedAt: string | null;
  checkError: string | null;
}

export interface FavoritesResponse {
  favorites: Favorite[];
  checking: boolean;
}

export type LibraryAction = 'download' | 'upload' | 'move' | 'delete_item' | 'delete_folder' | 'create_folder';

export interface LibraryEvent {
  id: number;
  createdAt: string;
  action: LibraryAction;
  itemCount: number;
  folderId: number | null;
  folderName: string;
  targetFolderId: number | null;
  targetFolderName: string | null;
  detail: string;
  folderExists: boolean;
  targetFolderExists: boolean;
}

export interface ActivityResponse {
  events: LibraryEvent[];
  nextBefore: number | null;
}

export interface PlatformSessionStatus {
  platform: Platform;
  source: 'web' | 'env' | 'none';
  accountName: string | null;
  verified: boolean;
  updatedAt: string | null;
  waitSeconds: number;
  // Set when the cookie should be replaced: rejected, or limited again and again.
  replaceReason: string | null;
  problemSince: string | null;
}

export interface StoriesResponse {
  status: 'available' | 'none' | 'login_required';
  items: Post[];
  cached: boolean;
  fetchedAt: string | null;
  rate: RateInfo | null;
}
