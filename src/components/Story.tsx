import React from 'react';
import { Post } from '../types';
import SelectToggle from './SelectToggle';
import { DownloadOverlay } from './DownloadStatus';
import { ItemDownloadState, isBusy } from './downloadState';

interface StoryProps {
  story: Post;
  isSelectable: boolean;
  isSelected: boolean;
  savedFolders: string[] | undefined;
  downloadState?: ItemDownloadState;
  onToggle: (id: string) => void;
}

const Story: React.FC<StoryProps> = ({
  story,
  isSelectable,
  isSelected,
  savedFolders,
  downloadState,
  onToggle,
}) => (
  <div className={`relative rounded-lg ${isSelected ? 'ring-4 ring-gray-900' : ''}`}>
    <img
      src={story.imageUrl}
      alt={story.caption || 'Story'}
      loading="lazy"
      className={`w-full h-40 object-cover rounded-lg bg-gray-200 ${savedFolders || downloadState === 'done' ? 'grayscale opacity-60' : ''}`}
    />
    {savedFolders && (
      <span className="absolute top-2 right-2 rounded bg-gray-900 px-1.5 py-0.5 text-xs font-semibold text-white">
        Tersimpan
      </span>
    )}
    {story.caption && (
      <div className="absolute bottom-0 left-0 right-0 rounded-b-lg bg-black/70 p-2">
        <p className="text-white text-sm truncate">{story.caption}</p>
      </div>
    )}
    <DownloadOverlay state={downloadState} className="rounded-lg" />
    {isSelectable && (
      <SelectToggle
        isSelected={isSelected}
        isDisabled={savedFolders !== undefined || isBusy(downloadState)}
        label={savedFolders ? `Story sudah di Library: ${savedFolders.join(', ')}` : 'Pilih story'}
        onToggle={() => onToggle(story.id)}
      />
    )}
  </div>
);

export default Story;
