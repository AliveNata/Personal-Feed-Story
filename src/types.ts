export type Platform = 'instagram' | 'tiktok';

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
}

export interface FeedResponse {
  platform: Platform;
  username: string;
  posts: Post[];
  cached: boolean;
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

export interface Folder {
  id: number;
  name: string;
  photoCount: number;
  videoCount: number;
  sizeBytes: number;
  createdAt: string;
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
