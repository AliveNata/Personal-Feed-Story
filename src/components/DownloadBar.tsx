import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { DownloadJob, Folder, Platform } from '../types';
import { ApiError, createDownload, fetchFolders, fetchJob } from '../utils/api';
import { TrackedJob } from './downloadState';
import { focusRing, primaryButton, secondaryButton } from './ui';

interface DownloadBarProps {
  platform: Platform;
  username: string;
  selectedIds: string[];
  onClearSelection: () => void;
  onJobFinished: () => void;
  // Every poll result, so the cards can show which post is downloading. null when closed.
  onJobUpdate: (tracked: TrackedJob | null) => void;
}

const JOB_POLL_MS = 2000;

const jobSummary = (job: DownloadJob): string => {
  if (job.status === 'queued') {
    return `Menunggu antrian, ${job.totalPosts} post`;
  }
  const progress = `${job.donePosts + job.failedPosts}/${job.totalPosts} post`;
  if (job.status === 'running') {
    return `Mengunduh ${progress}${job.failedPosts ? `, ${job.failedPosts} gagal` : ''}`;
  }
  const parts = [`${job.savedItems} file tersimpan`];
  if (job.skippedItems) parts.push(`${job.skippedItems} dilewati (sudah ada)`);
  if (job.failedPosts) parts.push(`${job.failedPosts} post gagal`);
  return `${job.status === 'done' ? 'Selesai' : 'Berhenti'}: ${parts.join(', ')}`;
};

const DownloadBar: React.FC<DownloadBarProps> = ({
  platform,
  username,
  selectedIds,
  onClearSelection,
  onJobFinished,
  onJobUpdate,
}) => {
  const [folderName, setFolderName] = useState('');
  const [folders, setFolders] = useState<Folder[]>([]);
  const [job, setJob] = useState<DownloadJob | null>(null);
  const [jobPostIds, setJobPostIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    fetchFolders()
      .then(setFolders)
      .catch((caught: unknown) => console.error('fetchFolders failed', caught));
  }, [job?.status]);

  useEffect(() => {
    onJobUpdate(job ? { job, postIds: jobPostIds } : null);
  }, [job, jobPostIds, onJobUpdate]);

  const jobStatus = job?.status;
  useEffect(() => {
    if (jobStatus === 'done' || jobStatus === 'failed') {
      onJobFinished();
    }
  }, [jobStatus, onJobFinished]);

  useEffect(() => {
    if (!job || job.status === 'done' || job.status === 'failed') {
      return;
    }
    const timer = window.setTimeout(() => {
      fetchJob(job.id)
        .then(setJob)
        .catch((caught: unknown) => {
          console.error('fetchJob failed', caught);
          setError('Gagal cek progress download. Cek di Library nanti.');
        });
    }, JOB_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [job]);

  const isJobActive = job !== null && (job.status === 'queued' || job.status === 'running');
  if (selectedIds.length === 0 && !job) {
    return null;
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      const created = await createDownload({
        folderName,
        platform,
        username,
        postIds: selectedIds,
      });
      setJobPostIds(selectedIds);
      setJob(created);
      onClearSelection();
    } catch (caught) {
      console.error('createDownload failed', caught);
      setError(caught instanceof ApiError ? caught.message : 'Gagal memulai download');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 border-t border-gray-300 bg-white shadow-[0_-4px_12px_rgba(0,0,0,0.08)]">
      <div className="container mx-auto px-4 py-3 flex flex-col gap-3">
        {job && (
          <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-3 text-sm">
            <span className="font-semibold text-gray-900">{jobSummary(job)}</span>
            {!isJobActive && (
              <>
                <Link to={`/library/${job.folderId}`} className={`underline text-gray-900 ${focusRing}`}>
                  Buka folder
                </Link>
                <button type="button" className="text-gray-700 underline" onClick={() => setJob(null)}>
                  Tutup
                </button>
              </>
            )}
            {job.errors.length > 0 && !isJobActive && (
              <details className="w-full text-gray-700">
                <summary className="cursor-pointer">Lihat error</summary>
                <ul className="mt-1 list-disc pl-5">
                  {job.errors.map((item) => (
                    <li key={item.postId} className="break-words">
                      {item.postId}: {item.message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        {selectedIds.length > 0 && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-2 md:flex-row md:items-end">
            <p className="text-sm font-semibold text-gray-900 md:mr-2 md:pb-3">
              {selectedIds.length} dipilih
            </p>
            <div className="flex-1 min-w-0">
              <label htmlFor="download-folder" className="block text-sm font-semibold text-gray-700 mb-1">
                Simpan ke folder
              </label>
              <input
                id="download-folder"
                list="download-folder-options"
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
                placeholder="Nama folder baru atau yang sudah ada"
                maxLength={100}
                required
                className={`w-full min-h-[44px] px-3 border border-gray-300 rounded-md text-gray-900 ${focusRing}`}
              />
              <datalist id="download-folder-options">
                {folders.map((folder) => (
                  <option key={folder.id} value={folder.name} />
                ))}
              </datalist>
            </div>
            <div className="flex gap-2">
              <button type="submit" className={`flex-1 md:flex-none ${primaryButton}`} disabled={isSubmitting || isJobActive}>
                {isSubmitting ? 'Memulai...' : `Download ${selectedIds.length} post`}
              </button>
              <button type="button" className={`flex-1 md:flex-none ${secondaryButton}`} onClick={onClearSelection}>
                Batal pilih
              </button>
            </div>
          </form>
        )}
        {isJobActive && selectedIds.length > 0 && (
          <p className="text-sm text-gray-700">Tunggu download sebelumnya selesai dulu.</p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
      </div>
    </div>
  );
};

export default DownloadBar;
