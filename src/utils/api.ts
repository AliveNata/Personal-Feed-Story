import { Post, User } from '../types';

const mockUser: User = {
  email: 'user@example.com',
};

const mockInstagramPosts: Post[] = [
  {
    id: '1',
    imageUrl: 'https://source.unsplash.com/random/800x600?instagram',
    caption: 'Beautiful sunset at the beach! 🌅 #nofilter',
    likes: 1234,
    comments: 56,
    shares: 7,
    timestamp: '2 hours ago',
    isStory: true,
  },
  {
    id: '2',
    imageUrl: 'https://source.unsplash.com/random/800x600?food',
    caption: 'Delicious brunch with friends 🍳🥑 #foodie',
    likes: 987,
    comments: 43,
    shares: 5,
    timestamp: '5 hours ago',
    isStory: false,
  },
  {
    id: '3',
    imageUrl: 'https://source.unsplash.com/random/800x600?travel',
    caption: 'Exploring new places! 🗺️✈️ #wanderlust',
    likes: 2345,
    comments: 78,
    shares: 12,
    timestamp: '1 day ago',
    isStory: false,
  },
];

const mockTikTokPosts: Post[] = [
  {
    id: '1',
    imageUrl: 'https://source.unsplash.com/random/800x600?dance',
    caption: 'New dance challenge! 💃 #TikTokDance',
    likes: 10000,
    comments: 500,
    shares: 1000,
    timestamp: '3 hours ago',
    isStory: true,
  },
  {
    id: '2',
    imageUrl: 'https://source.unsplash.com/random/800x600?comedy',
    caption: 'When your code finally works 😂 #ProgrammerHumor',
    likes: 50000,
    comments: 2000,
    shares: 5000,
    timestamp: '1 day ago',
    isStory: false,
  },
  {
    id: '3',
    imageUrl: 'https://source.unsplash.com/random/800x600?music',
    caption: 'Cover of my favorite song! 🎵 #MusicTikTok',
    likes: 25000,
    comments: 1500,
    shares: 3000,
    timestamp: '2 days ago',
    isStory: false,
  },
];

export const fetchMockData = async (platform: 'instagram' | 'tiktok', username: string): Promise<Post[]> => {
  await new Promise((resolve) => setTimeout(resolve, 500));
  return platform === 'instagram' ? mockInstagramPosts : mockTikTokPosts;
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