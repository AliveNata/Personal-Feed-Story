import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Folder } from '../types';
import { ApiError, deleteFolder, fetchFolders, formatBytes } from '../utils/api';
import { ConfirmDelete, Panel, focusRing, primaryButton } from './ui';

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; folders: Folder[] };

const dateFormat = new Intl.DateTimeFormat('id', { dateStyle: 'medium' });

const LibraryPage: React.FC = () => {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  const load = useCallback(() => {
    setState({ status: 'loading' });
    fetchFolders()
      .then((folders) => setState({ status: 'ready', folders }))
      .catch((caught: unknown) => {
        console.error('fetchFolders failed', caught);
        setState({
          status: 'error',
          message: caught instanceof ApiError ? caught.message : 'Gagal memuat Library',
        });
      });
  }, []);

  useEffect(load, [load]);

  const handleDelete = async (folderId: number) => {
    await deleteFolder(folderId);
    setState((previous) =>
      previous.status === 'ready'
        ? { status: 'ready', folders: previous.folders.filter((folder) => folder.id !== folderId) }
        : previous,
    );
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Library</h1>

      {state.status === 'loading' && (
        <Panel role="status">
          <p>Memuat folder...</p>
        </Panel>
      )}

      {state.status === 'error' && (
        <Panel role="alert" tone="error">
          <p className="mb-4 text-gray-800">{state.message}</p>
          <button type="button" className={primaryButton} onClick={load}>
            Coba lagi
          </button>
        </Panel>
      )}

      {state.status === 'ready' && state.folders.length === 0 && (
        <Panel>
          <p className="mb-2">Belum ada folder.</p>
          <p>
            Buka{' '}
            <Link to="/" className={`underline text-gray-900 ${focusRing}`}>
              Feed
            </Link>
            , centang post yang mau disimpan, lalu isi nama folder di bar bawah.
          </p>
        </Panel>
      )}

      {state.status === 'ready' && state.folders.length > 0 && (
        <ul className="bg-white rounded-lg shadow-md divide-y divide-gray-200">
          {state.folders.map((folder) => (
            <li key={folder.id} className="flex flex-col gap-3 p-4 md:flex-row md:items-center">
              <div className="min-w-0 flex-1">
                <Link
                  to={`/library/${folder.id}`}
                  className={`text-lg font-semibold text-gray-900 underline-offset-4 hover:underline break-words ${focusRing}`}
                >
                  {folder.name}
                </Link>
                <p className="text-sm text-gray-700">
                  {folder.photoCount} foto, {folder.videoCount} video, {formatBytes(folder.sizeBytes)}
                  {' · '}dibuat {dateFormat.format(new Date(folder.createdAt))}
                </p>
              </div>
              <ConfirmDelete
                label="Hapus folder"
                confirmText={`Hapus "${folder.name}" beserta semua isinya?`}
                onConfirm={() => handleDelete(folder.id)}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default LibraryPage;
