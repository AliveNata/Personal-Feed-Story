import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Play } from 'lucide-react';
import { Folder, MediaItem, MediaSource, MediaType } from '../types';
import {
  ApiError,
  deleteFolder,
  deleteItem,
  fetchFolder,
  fetchFolderItems,
  formatBytes,
} from '../utils/api';
import MediaViewer from './MediaViewer';
import TabBar from './TabBar';
import UploadDialog from './UploadDialog';
import { ConfirmDelete, Panel, focusRing, primaryButton, secondaryButton } from './ui';

const SOURCE_LABELS: Record<MediaSource, string> = {
  post: 'Post',
  story: 'Story',
  upload: 'Upload',
};
const TYPE_LABELS: Record<MediaType, string> = { photo: 'Foto', video: 'Video' };
const SOURCES: MediaSource[] = ['post', 'story', 'upload'];
const TYPES: MediaType[] = ['photo', 'video'];

const parseSource = (value: string | null): MediaSource =>
  value === 'story' || value === 'upload' ? value : 'post';

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
  const activeSource = parseSource(searchParams.get('source'));
  const [folder, setFolder] = useState<Folder | null>(null);
  const [folderError, setFolderError] = useState<string | null>(null);
  const [items, setItems] = useState<ItemsState>({ status: 'loading' });
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [isUploadOpen, setIsUploadOpen] = useState(false);

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
    fetchFolderItems(folderId, activeTab, activeSource)
      .then((data) => setItems({ status: 'ready', items: data }))
      .catch((caught: unknown) => {
        console.error('fetchFolderItems failed', caught);
        setItems({ status: 'error', message: errorMessage(caught, 'Gagal memuat file') });
      });
  }, [folderId, activeTab, activeSource]);

  useEffect(loadFolder, [loadFolder]);
  useEffect(loadItems, [loadItems]);

  const updateParams = (source: MediaSource, type: MediaType) => {
    setViewerIndex(null);
    const params: Record<string, string> = {};
    if (source !== 'post') params.source = source;
    if (type !== 'photo') params.tab = type;
    setSearchParams(params, { replace: true });
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

      <TabBar
        label="Sumber file"
        idPrefix="source"
        panelId="folder-tabpanel"
        active={activeSource}
        onSelect={(source) => updateParams(source, activeTab)}
        options={SOURCES.map((source) => ({
          value: source,
          label: SOURCE_LABELS[source],
          count: folder ? folder.counts[source].photo + folder.counts[source].video : undefined,
        }))}
      />
      <div className="mt-3 mb-4">
        <TabBar
          label="Jenis file"
          idPrefix="type"
          panelId="folder-tabpanel"
          size="small"
          active={activeTab}
          onSelect={(type) => updateParams(activeSource, type)}
          options={TYPES.map((type) => ({
            value: type,
            label: TYPE_LABELS[type],
            count: folder ? folder.counts[activeSource][type] : undefined,
          }))}
        />
      </div>

      <div id="folder-tabpanel" role="tabpanel" aria-labelledby={`source-${activeSource} type-${activeTab}`}>
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
            <p>
              Belum ada {TYPE_LABELS[activeTab].toLowerCase()} dari {SOURCE_LABELS[activeSource].toLowerCase()} di
              folder ini.
            </p>
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
