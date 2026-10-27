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
  email: string;
}
