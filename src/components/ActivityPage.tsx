import React, { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, Download, FolderX, MoveRight, Trash2, Upload } from 'lucide-react';
import { LibraryAction, LibraryEvent } from '../types';
import { ApiError, fetchActivity } from '../utils/api';
import { Panel, focusRing, primaryButton, secondaryButton } from './ui';

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; events: LibraryEvent[]; nextBefore: number | null; isLoadingMore: boolean };

// The icon tells the kind of change at a glance down a long list, the text says the rest.
const ACTION_ICONS: Record<LibraryAction, React.FC<{ className?: string }>> = {
  download: Download,
  upload: Upload,
  move: MoveRight,
  delete_item: Trash2,
  delete_folder: FolderX,
};

const ACTION_LABELS: Record<LibraryAction, string> = {
  download: 'Download',
  upload: 'Upload',
  move: 'Pindah',
  delete_item: 'Hapus file',
  delete_folder: 'Hapus folder',
};

const timeFormat = new Intl.DateTimeFormat('id', { dateStyle: 'medium', timeStyle: 'short' });

const FolderName: React.FC<{ id: number | null; name: string; exists: boolean }> = ({ id, name, exists }) =>
  exists && id ? (
    <Link to={`/library/${id}`} className={`font-semibold text-gray-900 underline ${focusRing}`}>
      {name}
    </Link>
  ) : (
    // Deleted since: keep the name readable, without a link to nowhere.
    <span className="font-semibold text-gray-900">
      {name} <span className="font-normal text-gray-700">(sudah dihapus)</span>
    </span>
  );

const Summary: React.FC<{ event: LibraryEvent }> = ({ event }) => {
  const files = `${event.itemCount} file`;
  const folder = <FolderName id={event.folderId} name={event.folderName} exists={event.folderExists} />;
  switch (event.action) {
    case 'download':
      return (
        <>
          {files} di-download ke {folder}
        </>
      );
    case 'upload':
      return (
        <>
          {files} di-upload ke {folder}
        </>
      );
    case 'delete_item':
      return (
        <>
          {files} dihapus dari {folder}
        </>
      );
    case 'delete_folder':
      return (
        <>
          Folder {folder} dihapus beserta {files}
        </>
      );
    case 'move':
      return (
        <span className="inline-flex flex-wrap items-center gap-x-1.5">
          {files} dipindah dari {folder}
          <ArrowRight className="h-4 w-4 text-gray-700" aria-label="ke" />
          <FolderName
            id={event.targetFolderId}
            name={event.targetFolderName ?? ''}
            exists={event.targetFolderExists}
          />
        </span>
      );
  }
};

const ActivityPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const folderParam = Number(searchParams.get('folder'));
  const folderId = Number.isFinite(folderParam) && folderParam > 0 ? folderParam : null;
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  const load = useCallback(() => {
    setState({ status: 'loading' });
    fetchActivity({ folderId })
      .then((response) =>
        setState({
          status: 'ready',
          events: response.events,
          nextBefore: response.nextBefore,
          isLoadingMore: false,
        }),
      )
      .catch((caught: unknown) => {
        console.error('fetchActivity failed', caught);
        setState({
          status: 'error',
          message: caught instanceof ApiError ? caught.message : 'Gagal memuat riwayat',
        });
      });
  }, [folderId]);

  useEffect(load, [load]);

  const loadMore = () => {
    if (state.status !== 'ready' || !state.nextBefore) return;
    setState({ ...state, isLoadingMore: true });
    fetchActivity({ folderId, before: state.nextBefore })
      .then((response) =>
        setState((previous) =>
          previous.status === 'ready'
            ? {
                status: 'ready',
                events: [...previous.events, ...response.events],
                nextBefore: response.nextBefore,
                isLoadingMore: false,
              }
            : previous,
        ),
      )
      .catch((caught: unknown) => {
        console.error('fetchActivity (more) failed', caught);
        setState((previous) =>
          previous.status === 'ready' ? { ...previous, isLoadingMore: false } : previous,
        );
      });
  };

  return (
    <div>
      <Link
        to={folderId ? `/library/${folderId}` : '/library'}
        className={`text-sm text-gray-700 underline ${focusRing}`}
      >
        {folderId ? 'Kembali ke folder' : 'Library'}
      </Link>
      <h1 className="mt-2 mb-1 text-2xl font-bold">Riwayat</h1>
      <p className="mb-6 text-sm text-gray-700">
        {folderId ? 'Perubahan yang menyangkut folder ini.' : 'Semua perubahan di Library, terbaru di atas.'}
      </p>

      {state.status === 'loading' && (
        <Panel role="status">
          <p>Memuat riwayat...</p>
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
      {state.status === 'ready' && state.events.length === 0 && (
        <Panel>
          <p>Belum ada perubahan yang tercatat. Riwayat mulai dicatat sejak fitur ini dipasang.</p>
        </Panel>
      )}
      {state.status === 'ready' && state.events.length > 0 && (
        <>
          <ol className="divide-y divide-gray-200 rounded-lg bg-white shadow-md">
            {state.events.map((event) => {
              const Icon = ACTION_ICONS[event.action];
              return (
                <li key={event.id} className="flex gap-3 p-4">
                  <span
                    className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-800"
                    title={ACTION_LABELS[event.action]}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-gray-800">
                      <span className="sr-only">{ACTION_LABELS[event.action]}: </span>
                      <Summary event={event} />
                    </p>
                    {event.detail && (
                      <p className="mt-0.5 break-words text-sm text-gray-700">{event.detail}</p>
                    )}
                  </div>
                  <time dateTime={event.createdAt} className="shrink-0 text-right text-xs text-gray-700">
                    {timeFormat.format(new Date(event.createdAt))}
                  </time>
                </li>
              );
            })}
          </ol>
          <div className="mt-4 flex justify-center">
            {state.nextBefore ? (
              <button
                type="button"
                className={secondaryButton}
                onClick={loadMore}
                disabled={state.isLoadingMore}
              >
                {state.isLoadingMore ? 'Memuat...' : 'Muat riwayat lama'}
              </button>
            ) : (
              <p className="text-sm text-gray-700">Itu semua riwayatnya.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default ActivityPage;
