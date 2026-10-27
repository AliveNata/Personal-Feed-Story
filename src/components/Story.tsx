import React from 'react';
import { Post } from '../types';
import SelectToggle from './SelectToggle';

interface StoryProps {
  story: Post;
  isSelectable: boolean;
  isSelected: boolean;
  onToggle: (id: string) => void;
}

const Story: React.FC<StoryProps> = ({ story, isSelectable, isSelected, onToggle }) => (
  <div className={`relative rounded-lg ${isSelected ? 'ring-4 ring-gray-900' : ''}`}>
    <img
      src={story.imageUrl}
      alt={story.caption || 'Story'}
      loading="lazy"
      className="w-full h-40 object-cover rounded-lg bg-gray-200"
    />
    {story.caption && (
      <div className="absolute bottom-0 left-0 right-0 rounded-b-lg bg-black/70 p-2">
        <p className="text-white text-sm truncate">{story.caption}</p>
      </div>
    )}
    {isSelectable && (
      <SelectToggle
        isSelected={isSelected}
        label="Pilih story"
        onToggle={() => onToggle(story.id)}
      />
    )}
  </div>
);

export default Story;
