import React, { useEffect, useRef, useState } from 'react';
import { Upload, X } from 'lucide-react';
import { MediaType } from '../types';
import { ApiError, formatBytes, uploadFile } from '../utils/api';
import { focusRing, primaryButton, secondaryButton } from './ui';

interface UploadDialogProps {
  folderId: number;
  folderName: string;
  onClose: () => void;
  onUploaded: () => void;
}

type EntryStatus = 'pending' | 'uploading' | 'saved' | 'skipped' | 'failed' | 'unsupported';

interface Entry {
  key: string;
  file: File;
  mediaType: MediaType | null;
  status: EntryStatus;
  progress: number;
  message?: string;
}

// Same list the backend accepts. Extension first, because some browsers send an empty
// MIME type for formats like .heic.
const PHOTO_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'heic', 'avif'];
const VIDEO_EXTENSIONS = ['mp4', 'm4v', 'mov', 'webm'];
const ACCEPT = [...PHOTO_EXTENSIONS, ...VIDEO_EXTENSIONS].map((ext) => `.${ext}`).join(',');

const detectType = (file: File): MediaType | null => {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  if (PHOTO_EXTENSIONS.includes(extension)) return 'photo';
  if (VIDEO_EXTENSIONS.includes(extension)) return 'video';
  return null;
};

const STATUS_TEXT: Record<EntryStatus, string> = {
  pending: 'Siap',
  uploading: 'Mengupload',
  saved: 'Tersimpan',
  skipped: 'Sudah ada, dilewati',
  failed: 'Gagal',
  unsupported: 'Bukan foto atau video',
};

const UploadDialog: React.FC<UploadDialogProps> = ({ folderId, folderName, onClose, onUploaded }) => {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const added = Array.from(files).map((file): Entry => {
      const mediaType = detectType(file);
      return {
        key: `${file.name}-${file.size}-${file.lastModified}-${Math.random()}`,
        file,
        mediaType,
        status: mediaType ? 'pending' : 'unsupported',
        progress: 0,
      };
    });
    setEntries((previous) => [...previous, ...added]);
  };

  const update = (key: string, patch: Partial<Entry>) =>
    setEntries((previous) => previous.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)));

  const pending = entries.filter((entry) => entry.status === 'pending');
  const photoCount = pending.filter((entry) => entry.mediaType === 'photo').length;
  const videoCount = pending.length - photoCount;

  const startUpload = async () => {
    setIsUploading(true);
    let savedAny = false;
    // One at a time: keeps progress readable and avoids hammering the disk with big videos.
    for (const entry of pending) {
      update(entry.key, { status: 'uploading', progress: 0 });
      try {
        const result = await uploadFile(folderId, entry.file, (progress) => update(entry.key, { progress }));
        update(entry.key, { status: result.status, progress: 1 });
        savedAny = savedAny || result.status === 'saved';
      } catch (error) {
        console.error('upload failed', entry.file.name, error);
        update(entry.key, {
          status: 'failed',
          message: error instanceof ApiError ? error.message : 'Upload gagal',
        });
      }
    }
    setIsUploading(false);
    if (savedAny) onUploaded();
  };

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
    if (!isUploading) addFiles(event.dataTransfer.files);
  };

  const isDone = entries.length > 0 && pending.length === 0 && !isUploading;

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      onCancel={(event) => {
        // Escape during an upload would hide progress while files are still sending.
        if (isUploading) event.preventDefault();
      }}
      aria-labelledby="upload-title"
      className="w-[min(40rem,calc(100vw-2rem))] rounded-lg p-0 shadow-xl backdrop:bg-black/60"
    >
      <div className="flex items-center justify-between gap-3 border-b border-gray-200 p-4">
        <h2 id="upload-title" className="text-lg font-bold text-gray-900 break-words">
          Tambah file ke "{folderName}"
        </h2>
        <button
          type="button"
          onClick={() => dialogRef.current?.close()}
          disabled={isUploading}
          aria-label="Tutup"
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-gray-700 hover:bg-gray-100 disabled:opacity-40 ${focusRing}`}
        >
          <X className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>

      <div className="flex flex-col gap-4 p-4">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          className={`flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-6 text-center ${
            isDragging ? 'border-gray-900 bg-gray-100' : 'border-gray-300'
          }`}
        >
          <Upload className="h-8 w-8 text-gray-700" aria-hidden="true" />
          <p className="text-gray-800">Tarik foto atau video ke sini</p>
          <p className="text-sm text-gray-700">atau</p>
          <button
            type="button"
            className={secondaryButton}
            onClick={() => inputRef.current?.click()}
            disabled={isUploading}
          >
            Pilih file
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            onChange={(event) => {
              addFiles(event.target.files);
              event.target.value = '';
            }}
          />
          <p className="text-xs text-gray-700">
            Foto: {PHOTO_EXTENSIONS.join(', ')}. Video: {VIDEO_EXTENSIONS.join(', ')}.
          </p>
        </div>

        {entries.length > 0 && (
          <ul className="max-h-64 overflow-y-auto divide-y divide-gray-200 rounded-md border border-gray-200">
            {entries.map((entry) => (
              <li key={entry.key} className="flex flex-col gap-1 p-3">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm text-gray-900" title={entry.file.name}>
                    {entry.file.name}
                  </span>
                  {entry.mediaType && (
                    <span className="shrink-0 rounded bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-800">
                      {entry.mediaType === 'photo' ? 'Foto' : 'Video'}
                    </span>
                  )}
                  <span className="shrink-0 text-xs text-gray-700">{formatBytes(entry.file.size)}</span>
                </div>
                <p
                  className={`text-xs ${
                    entry.status === 'failed' || entry.status === 'unsupported' ? 'text-red-700' : 'text-gray-700'
                  }`}
                >
                  {entry.status === 'uploading'
                    ? `${STATUS_TEXT.uploading} ${Math.round(entry.progress * 100)}%`
                    : (entry.message ?? STATUS_TEXT[entry.status])}
                </p>
                {entry.status === 'uploading' && (
                  <div
                    role="progressbar"
                    aria-label={`Progress ${entry.file.name}`}
                    aria-valuenow={Math.round(entry.progress * 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    className="h-1.5 overflow-hidden rounded bg-gray-200"
                  >
                    <div className="h-full bg-gray-900" style={{ width: `${entry.progress * 100}%` }} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2">
          {pending.length > 0 && (
            <p className="mr-auto text-sm text-gray-700">
              {photoCount} foto, {videoCount} video
            </p>
          )}
          {isDone ? (
            <button type="button" className={primaryButton} onClick={() => dialogRef.current?.close()}>
              Selesai
            </button>
          ) : (
            <button
              type="button"
              className={primaryButton}
              onClick={startUpload}
              disabled={pending.length === 0 || isUploading}
            >
              {isUploading ? 'Mengupload...' : pending.length ? `Upload ${pending.length} file` : 'Upload'}
            </button>
          )}
        </div>
      </div>
    </dialog>
  );
};

export default UploadDialog;
