import { DownloadJob } from '../types';

/** Where a post stands inside the download job. "done" shows grey before the Library lookup confirms it. */
export type ItemDownloadState = 'queued' | 'downloading' | 'done' | 'failed';

export interface TrackedJob {
  job: DownloadJob;
  // Same order the worker processes them in, so the index tells which one is running.
  postIds: string[];
}

export const isBusy = (state: ItemDownloadState | undefined): boolean =>
  state === 'queued' || state === 'downloading' || state === 'done';

export const downloadStates = (tracked: TrackedJob | null): Record<string, ItemDownloadState> => {
  if (!tracked) return {};
  const { job, postIds } = tracked;
  const failed = new Set(job.errors.map((error) => error.postId));
  const processed = job.donePosts + job.failedPosts;
  const isActive = job.status === 'queued' || job.status === 'running';
  const states: Record<string, ItemDownloadState> = {};
  // Posts run in order and each ends done or with an error entry, except after a rate limit,
  // where the rest is counted failed without entries. So only the first donePosts are done.
  let doneLeft = job.donePosts;
  postIds.forEach((postId, index) => {
    if (failed.has(postId)) {
      states[postId] = 'failed';
    } else if (doneLeft > 0) {
      states[postId] = 'done';
      doneLeft -= 1;
    } else if (isActive && index >= processed) {
      states[postId] = job.status === 'running' && index === processed ? 'downloading' : 'queued';
    }
  });
  return states;
};
