import { MediaItem } from '../types';

export type LibrarySort = 'added_desc' | 'added_asc' | 'name_asc' | 'name_desc' | 'size_desc' | 'size_asc';

export const SORT_OPTIONS: { value: LibrarySort; label: string }[] = [
  { value: 'added_desc', label: 'Terbaru ditambah' },
  { value: 'added_asc', label: 'Terlama ditambah' },
  { value: 'name_asc', label: 'Nama A sampai Z' },
  { value: 'name_desc', label: 'Nama Z sampai A' },
  { value: 'size_desc', label: 'Ukuran terbesar' },
  { value: 'size_asc', label: 'Ukuran terkecil' },
];

const SORT_KEY = 'library.sort';
const DEFAULT_SORT: LibrarySort = 'added_desc';

export const readLibrarySort = (): LibrarySort => {
  try {
    const stored = window.localStorage.getItem(SORT_KEY);
    return SORT_OPTIONS.find((option) => option.value === stored)?.value ?? DEFAULT_SORT;
  } catch (error) {
    console.warn('could not read library sort from localStorage', error);
    return DEFAULT_SORT;
  }
};

export const saveLibrarySort = (sort: LibrarySort): void => {
  try {
    window.localStorage.setItem(SORT_KEY, sort);
  } catch (error) {
    console.warn('could not save library sort to localStorage', error);
  }
};

// Uploads keep their original file name in the caption, scraped posts are named by account and post.
const itemName = (item: MediaItem): string =>
  item.platform === 'upload' && item.caption
    ? item.caption
    : `${item.authorUsername} ${item.sourcePostId} ${item.itemIndex}`;

// numeric: IMG_9 before IMG_10, the way file explorers sort.
const nameCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

const compare: Record<LibrarySort, (a: MediaItem, b: MediaItem) => number> = {
  added_desc: (a, b) => b.addedAt.localeCompare(a.addedAt) || b.id - a.id,
  added_asc: (a, b) => a.addedAt.localeCompare(b.addedAt) || a.id - b.id,
  name_asc: (a, b) => nameCollator.compare(itemName(a), itemName(b)) || a.id - b.id,
  name_desc: (a, b) => nameCollator.compare(itemName(b), itemName(a)) || b.id - a.id,
  size_desc: (a, b) => b.sizeBytes - a.sizeBytes || b.id - a.id,
  size_asc: (a, b) => a.sizeBytes - b.sizeBytes || a.id - b.id,
};

export const sortItems = (items: MediaItem[], sort: LibrarySort): MediaItem[] => [...items].sort(compare[sort]);
