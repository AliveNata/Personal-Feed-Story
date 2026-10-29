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
  downloadPercent?: number | null;
  // Reels are portrait video covers, a taller tile shows more of them.
  kind?: 'story' | 'reel';
  onToggle: (id: string) => void;
}

const Story: React.FC<StoryProps> = ({
  story,
  isSelectable,
  isSelected,
  savedFolders,
  downloadState,
  downloadPercent = null,
  kind = 'story',
  onToggle,
}) => (
  <div className={`relative rounded-lg ${isSelected ? 'ring-4 ring-gray-900' : ''}`}>
    <img
      src={story.imageUrl}
      alt={story.caption || 'Story'}
      loading="lazy"
      className={`w-full ${kind === 'reel' ? 'h-64' : 'h-40'} object-cover rounded-lg bg-gray-200 ${savedFolders || downloadState === 'done' ? 'grayscale opacity-60' : ''}`}
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
    <DownloadOverlay state={downloadState} percent={downloadPercent} className="rounded-lg" />
    {isSelectable && (
      <SelectToggle
        isSelected={isSelected}
        isDisabled={savedFolders !== undefined || isBusy(downloadState)}
        label={
          savedFolders
            ? `${kind === 'reel' ? 'Reel' : 'Story'} sudah di Library: ${savedFolders.join(', ')}`
            : `Pilih ${kind}`
        }
        onToggle={() => onToggle(story.id)}
      />
    )}
  </div>
);

export default Story;
