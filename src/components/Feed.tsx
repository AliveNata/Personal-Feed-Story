import React from 'react';
import { Heart, MessageCircle, Share2 } from 'lucide-react';
import { Post, User } from '../types';
import Story from './Story';

interface FeedProps {
  posts: Post[];
  platform: 'instagram' | 'tiktok';
  user: User;
}

const Feed: React.FC<FeedProps> = ({ posts, platform, user }) => {
  const stories = posts.filter(post => post.isStory);
  const regularPosts = posts.filter(post => !post.isStory);

  return (
    <div>
      {stories.length > 0 && (
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4">Stories</h2>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-4">
            {stories.map((story) => (
              <Story key={story.id} story={story} user={user} />
            ))}
          </div>
        </div>
      )}

      <h2 className="text-2xl font-bold mb-4">Feed</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
        {regularPosts.map((post) => (
          <div key={post.id} className="bg-white rounded-lg shadow-md overflow-hidden">
            <img src={post.imageUrl} alt={post.caption} className="w-full h-64 object-cover" />
            <div className="p-4">
              <p className="text-gray-800 mb-2">{post.caption}</p>
              <div className="flex items-center justify-between text-gray-600">
                <div className="flex items-center">
                  <Heart className="w-5 h-5 mr-1" />
                  <span>{post.likes}</span>
                </div>
                <div className="flex items-center">
                  <MessageCircle className="w-5 h-5 mr-1" />
                  <span>{post.comments}</span>
                </div>
                <div className="flex items-center">
                  <Share2 className="w-5 h-5 mr-1" />
                  <span>{post.shares}</span>
                </div>
              </div>
            </div>
            <div className="bg-gray-100 px-4 py-2 text-sm text-gray-600">
              {platform === 'instagram' ? 'Instagram' : 'TikTok'} • {post.timestamp}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Feed;