import React from 'react';
import { Save } from 'lucide-react';
import { Post, User } from '../types';
import { saveStoryToGDrive } from '../utils/api';

interface StoryProps {
  story: Post;
  user: User;
}

const Story: React.FC<StoryProps> = ({ story, user }) => {
  const handleSave = async () => {
    try {
      const success = await saveStoryToGDrive(story, user.email);
      if (success) {
        alert('Story saved to Google Drive successfully!');
      } else {
        alert('Failed to save story to Google Drive.');
      }
    } catch (error) {
      console.error('Error saving story:', error);
      alert('An error occurred while saving the story.');
    }
  };

  return (
    <div className="relative">
      <img src={story.imageUrl} alt={story.caption} className="w-full h-40 object-cover rounded-lg" />
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black to-transparent p-2">
        <p className="text-white text-sm truncate">{story.caption}</p>
      </div>
      <button
        onClick={handleSave}
        className="absolute top-2 right-2 bg-white rounded-full p-1 shadow-md hover:bg-gray-100"
        title="Save to Google Drive"
      >
        <Save className="w-4 h-4 text-gray-800" />
      </button>
    </div>
  );
};

export default Story;