import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Play } from 'lucide-react';
import { Folder, MediaItem, MediaType } from '../types';
import {
  ApiError,
  deleteFolder,
  deleteItem,
  fetchFolder,
  fetchFolderItems,
  formatBytes,
} from '../utils/api';
import MediaViewer from './MediaViewer';
import UploadDialog from './UploadDialog';
import { ConfirmDelete, Panel, focusRing, primaryButton, secondaryButton } from './ui';

const TABS: { type: MediaType; label: string }[] = [
  { type: 'photo', label: 'Foto' },
  { type: 'video', label: 'Video' },
];

type ItemsState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: MediaItem[] };

const errorMessage = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? caught.message : fallback;

const FolderPage: React.FC = () => {
  const folderId = Number(useParams().folderId);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab: MediaType = searchParams.get('tab') === 'video' ? 'video' : 'photo';
  const [folder, setFolder] = useState<Folder | null>(null);
  const [folderError, setFolderError] = useState<string | null>(null);
  const [items, setItems] = useState<ItemsState>({ status: 'loading' });
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const loadFolder = useCallback(() => {
    fetchFolder(folderId)
      .then((data) => {
        setFolder(data);
        setFolderError(null);
      })
      .catch((caught: unknown) => {
        console.error('fetchFolder failed', caught);
        setFolderError(errorMessage(caught, 'Gagal memuat folder'));
      });
  }, [folderId]);

  const loadItems = useCallback(() => {
    setItems({ status: 'loading' });
    fetchFolderItems(folderId, activeTab)
      .then((data) => setItems({ status: 'ready', items: data }))
      .catch((caught: unknown) => {
        console.error('fetchFolderItems failed', caught);
        setItems({ status: 'error', message: errorMessage(caught, 'Gagal memuat file') });
      });
  }, [folderId, activeTab]);

  useEffect(loadFolder, [loadFolder]);
  useEffect(loadItems, [loadItems]);

  const selectTab = (type: MediaType) => {
    setViewerIndex(null);
    setSearchParams(type === 'photo' ? {} : { tab: type }, { replace: true });
  };

  const handleTabKey = (event: React.KeyboardEvent, index: number) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
      return;
    }
    const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length;
    selectTab(TABS[next].type);
    tabRefs.current[next]?.focus();
  };

  const handleDeleteItem = async (item: MediaItem) => {
    await deleteItem(item.id);
    if (items.status === 'ready') {
      const remaining = items.items.filter((candidate) => candidate.id !== item.id);
      setItems({ status: 'ready', items: remaining });
      setViewerIndex((index) =>
        remaining.length === 0 || index === null ? null : Math.min(index, remaining.length - 1),
      );
    }
    loadFolder();
  };

  if (folderError) {
    return (
      <Panel role="alert" tone="error">
        <p className="mb-4 text-gray-800">{folderError}</p>
        <Link to="/library" className={`underline text-gray-900 ${focusRing}`}>
          Kembali ke Library
        </Link>
      </Panel>
    );
  }

  const readyItems = items.status === 'ready' ? items.items : [];

  return (
    <div>
      <Link to="/library" className={`text-sm text-gray-700 underline ${focusRing}`}>
        Library
      </Link>
      <div className="mt-2 mb-6 flex flex-col gap-3 md:flex-row md:items-end">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold break-words">{folder?.name ?? 'Memuat...'}</h1>
          {folder && (
            <p className="text-sm text-gray-700">
              {folder.photoCount} foto, {folder.videoCount} video, {formatBytes(folder.sizeBytes)}
            </p>
          )}
        </div>
        {folder && (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={secondaryButton} onClick={() => setIsUploadOpen(true)}>
              Tambah file
            </button>
            <ConfirmDelete
              label="Hapus folder"
              confirmText={`Hapus "${folder.name}" beserta semua isinya?`}
              onConfirm={async () => {
                await deleteFolder(folder.id);
                navigate('/library', { replace: true });
              }}
            />
          </div>
        )}
      </div>

      <div role="tablist" aria-label="Jenis file" className="mb-4 flex border-b border-gray-300">
        {TABS.map((tab, index) => {
          const count = folder ? (tab.type === 'photo' ? folder.photoCount : folder.videoCount) : null;
          const isActive = tab.type === activeTab;
          return (
            <button
              key={tab.type}
              ref={(element) => {
                tabRefs.current[index] = element;
              }}
              type="button"
              role="tab"
              id={`tab-${tab.type}`}
              aria-selected={isActive}
              aria-controls="folder-tabpanel"
              tabIndex={isActive ? 0 : -1}
              onClick={() => selectTab(tab.type)}
              onKeyDown={(event) => handleTabKey(event, index)}
              className={`-mb-px min-h-[44px] px-5 text-sm font-semibold border-b-2 ${focusRing} ${
                isActive ? 'border-gray-900 text-gray-900' : 'border-transparent text-gray-700 hover:text-gray-900'
              }`}
            >
              {tab.label}
              {count !== null && <span className="ml-1 font-normal">({count})</span>}
            </button>
          );
        })}
      </div>

      <div id="folder-tabpanel" role="tabpanel" aria-labelledby={`tab-${activeTab}`}>
        {items.status === 'loading' && (
          <Panel role="status">
            <p>Memuat file...</p>
          </Panel>
        )}
        {items.status === 'error' && (
          <Panel role="alert" tone="error">
            <p className="mb-4 text-gray-800">{items.message}</p>
            <button type="button" className={primaryButton} onClick={loadItems}>
              Coba lagi
            </button>
          </Panel>
        )}
        {items.status === 'ready' && readyItems.length === 0 && (
          <Panel>
            <p>Belum ada {activeTab === 'photo' ? 'foto' : 'video'} di folder ini.</p>
          </Panel>
        )}
        {readyItems.length > 0 && (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {readyItems.map((item, index) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setViewerIndex(index)}
                  aria-label={`Buka ${item.mediaType === 'video' ? 'video' : 'foto'} dari @${item.authorUsername}`}
                  className={`relative block aspect-square w-full overflow-hidden rounded-md bg-gray-200 ${focusRing}`}
                >
                  {item.thumbnailUrl ? (
                    <img src={item.thumbnailUrl} alt="" loading="lazy" className="h-full w-full object-cover" />
                  ) : (
                    <span className="flex h-full items-center justify-center p-2 text-center text-xs text-gray-700">
                      Tidak ada preview
                    </span>
                  )}
                  {item.mediaType === 'video' && (
                    <span className="absolute bottom-2 right-2 rounded-full bg-black/70 p-1.5 text-white">
                      <Play className="h-4 w-4" aria-hidden="true" />
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {isUploadOpen && folder && (
        <UploadDialog
          folderId={folder.id}
          folderName={folder.name}
          onClose={() => setIsUploadOpen(false)}
          onUploaded={() => {
            loadFolder();
            loadItems();
          }}
        />
      )}

      {viewerIndex !== null && readyItems[viewerIndex] && (
        <MediaViewer
          item={readyItems[viewerIndex]}
          position={viewerIndex + 1}
          total={readyItems.length}
          onClose={() => setViewerIndex(null)}
          onPrevious={viewerIndex > 0 ? () => setViewerIndex(viewerIndex - 1) : undefined}
          onNext={viewerIndex < readyItems.length - 1 ? () => setViewerIndex(viewerIndex + 1) : undefined}
          onDelete={handleDeleteItem}
        />
      )}
    </div>
  );
};

export default FolderPage;
