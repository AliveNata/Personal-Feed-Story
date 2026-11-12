import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Download, FolderPlus, Trash2 } from 'lucide-react';
import { Folder } from '../types';
import { ApiError, deleteFolder, fetchFolders, folderZipUrl, formatBytes } from '../utils/api';
import { FOLDER_SORT_OPTIONS, FolderSort, readFolderSort, saveFolderSort, sortFolders } from '../utils/folderSort';
import FolderBackdrop from './FolderBackdrop';
import NewFolderForm from './NewFolderForm';
import UploadDialog from './UploadDialog';
import { ConfirmDelete, IconAction, Panel, focusRing, primaryButton, secondaryButton } from './ui';

type LoadState =
  { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; folders: Folder[] };

const dateFormat = new Intl.DateTimeFormat('id', { dateStyle: 'medium' });

const LibraryPage: React.FC = () => {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [uploadTarget, setUploadTarget] = useState<Folder | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [sort, setSort] = useState<FolderSort>(readFolderSort);
  const folders = useMemo(() => (state.status === 'ready' ? sortFolders(state.folders, sort) : []), [state, sort]);

  const load = useCallback((silent = false) => {
    if (!silent) setState({ status: 'loading' });
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

  useEffect(() => load(), [load]);

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
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Library</h1>
        <div className="flex flex-wrap items-center gap-2">
          {folders.length > 1 && (
            <div className="flex items-center gap-2">
              <label htmlFor="folder-sort" className="sr-only text-sm font-semibold text-gray-700 sm:not-sr-only">
                Urutkan
              </label>
              <select
                id="folder-sort"
                value={sort}
                onChange={(event) => {
                  const next = event.target.value as FolderSort;
                  setSort(next);
                  saveFolderSort(next);
                }}
                className={`min-h-[44px] rounded-md border border-gray-300 bg-white px-3 text-sm text-gray-900 ${focusRing}`}
              >
                {FOLDER_SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <Link to="/library/activity" className={`inline-flex items-center ${secondaryButton}`}>
            Riwayat
          </Link>
          {!isCreating && (
            <button type="button" className={primaryButton} onClick={() => setIsCreating(true)}>
              Folder baru
            </button>
          )}
        </div>
      </div>

      {isCreating && (
        <NewFolderForm
          onCancel={() => setIsCreating(false)}
          onCreated={(folder) => {
            setIsCreating(false);
            // The sort puts the new folder where it belongs.
            setState((previous) =>
              previous.status === 'ready'
                ? { status: 'ready', folders: [folder, ...previous.folders] }
                : previous,
            );
          }}
        />
      )}

      {state.status === 'loading' && (
        <Panel role="status">
          <p>Memuat folder...</p>
        </Panel>
      )}

      {state.status === 'error' && (
        <Panel role="alert" tone="error">
          <p className="mb-4 text-gray-800">{state.message}</p>
          <button type="button" className={primaryButton} onClick={() => load()}>
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
        <ul className="overflow-hidden bg-white rounded-lg shadow-md divide-y divide-gray-200">
          {folders.map((folder) => {
            const hasBackdrop = folder.previewUrls.length > 0;
            return (
              <li
                key={folder.id}
                className={`relative flex flex-col gap-3 p-4 md:flex-row md:items-center ${hasBackdrop ? 'bg-gray-950 py-6' : ''}`}
              >
                {hasBackdrop && <FolderBackdrop urls={folder.previewUrls} />}
                <div className="relative min-w-0 flex-1">
                  <Link
                    to={`/library/${folder.slug}`}
                    className={`text-lg font-semibold underline-offset-4 hover:underline break-words ${
                      hasBackdrop
                        ? 'text-white focus-visible:ring-white focus-visible:ring-offset-gray-950'
                        : 'text-gray-900'
                    } ${focusRing}`}
                  >
                    {folder.name}
                  </Link>
                  <p className={`text-sm ${hasBackdrop ? 'text-gray-200' : 'text-gray-700'}`}>
                    {folder.photoCount} foto, {folder.videoCount} video, {formatBytes(folder.sizeBytes)}
                    {' · '}dibuat {dateFormat.format(new Date(folder.createdAt))}
                  </p>
                </div>
                <div className="relative flex flex-wrap items-center gap-2">
                  <IconAction
                    label="Tambah file"
                    tone="green"
                    isOnDark={hasBackdrop}
                    icon={<FolderPlus className="h-6 w-6" aria-hidden="true" />}
                    onClick={() => setUploadTarget(folder)}
                  />
                  {folder.photoCount + folder.videoCount > 0 && (
                    <IconAction
                      label="Download folder (ZIP)"
                      tone="blue"
                      isOnDark={hasBackdrop}
                      icon={<Download className="h-6 w-6" aria-hidden="true" />}
                      href={folderZipUrl(folder.id)}
                    />
                  )}
                  <ConfirmDelete
                    label="Hapus folder"
                    confirmText={`Hapus "${folder.name}" beserta semua isinya?`}
                    onConfirm={() => handleDelete(folder.id)}
                    isOnDark={hasBackdrop}
                    icon={<Trash2 className="h-6 w-6" aria-hidden="true" />}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {uploadTarget && (
        <UploadDialog
          folderId={uploadTarget.id}
          folderName={uploadTarget.name}
          onClose={() => setUploadTarget(null)}
          onUploaded={() => load(true)}
        />
      )}
    </div>
  );
};

export default LibraryPage;
