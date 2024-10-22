export interface Post {
  id: string;
  imageUrl: string;
  caption: string;
  likes: number;
  comments: number;
  shares: number;
  timestamp: string;
  isStory: boolean;
}

export interface User {
  email: string;
}