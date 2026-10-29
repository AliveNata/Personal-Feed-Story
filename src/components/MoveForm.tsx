import React, { useEffect, useState } from 'react';
import { Folder } from '../types';
import { ApiError, MoveResult, fetchFolders, moveItems } from '../utils/api';
import FolderPicker from './FolderPicker';
import { primaryButton, secondaryButton } from './ui';

interface MoveFormProps {
  itemIds: number[];
  currentFolderId: number;
  // id must be unique on the page: the form shows up in the viewer and in the bottom bar.
  inputId: string;
  onMoved: (result: MoveResult) => void;
  onCancel: () => void;
}

/** Pick an existing folder or type a new name, then move the files there. */
const MoveForm: React.FC<MoveFormProps> = ({ itemIds, currentFolderId, inputId, onMoved, onCancel }) => {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderName, setFolderName] = useState('');
  const [isMoving, setIsMoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFolders()
      // Moving into the folder the files are already in would do nothing.
      .then((all) => setFolders(all.filter((folder) => folder.id !== currentFolderId)))
      .catch((caught: unknown) => console.error('fetchFolders failed', caught));
  }, [currentFolderId]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsMoving(true);
    setError(null);
    try {
      onMoved(await moveItems(itemIds, folderName));
    } catch (caught) {
      console.error('moveItems failed', caught);
      setError(caught instanceof ApiError ? caught.message : 'Gagal memindahkan file');
      setIsMoving(false);
    }
  };

  const count = itemIds.length;

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 md:flex-row md:items-end">
      <div className="min-w-0 flex-1">
        <label htmlFor={inputId} className="mb-1 block text-sm font-semibold text-gray-700">
          Pindahkan ke folder
        </label>
        <FolderPicker id={inputId} value={folderName} folders={folders} onChange={setFolderName} />
      </div>
      <div className="flex gap-2">
        <button type="submit" className={`flex-1 md:flex-none ${primaryButton}`} disabled={isMoving}>
          {isMoving ? 'Memindahkan...' : `Pindahkan ${count} file`}
        </button>
        <button type="button" className={`flex-1 md:flex-none ${secondaryButton}`} onClick={onCancel}>
          Batal
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700 md:basis-full">
          {error}
        </p>
      )}
    </form>
  );
};

export default MoveForm;
