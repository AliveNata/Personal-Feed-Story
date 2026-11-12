import { Folder } from '../types';

export type FolderSort = 'created_desc' | 'created_asc' | 'name_asc' | 'name_desc' | 'size_desc' | 'size_asc';

export const FOLDER_SORT_OPTIONS: { value: FolderSort; label: string }[] = [
  { value: 'created_desc', label: 'Terbaru dibuat' },
  { value: 'created_asc', label: 'Terlama dibuat' },
  { value: 'name_asc', label: 'Nama A sampai Z' },
  { value: 'name_desc', label: 'Nama Z sampai A' },
  { value: 'size_desc', label: 'Ukuran terbesar' },
  { value: 'size_asc', label: 'Ukuran terkecil' },
];

const SORT_KEY = 'library.folderSort';
const DEFAULT_SORT: FolderSort = 'created_desc';

export const readFolderSort = (): FolderSort => {
  try {
    const stored = window.localStorage.getItem(SORT_KEY);
    return FOLDER_SORT_OPTIONS.find((option) => option.value === stored)?.value ?? DEFAULT_SORT;
  } catch (error) {
    console.warn('could not read folder sort from localStorage', error);
    return DEFAULT_SORT;
  }
};

export const saveFolderSort = (sort: FolderSort): void => {
  try {
    window.localStorage.setItem(SORT_KEY, sort);
  } catch (error) {
    console.warn('could not save folder sort to localStorage', error);
  }
};

// numeric: "Folder 9" before "Folder 10".
const nameCollator = new Intl.Collator('id', { numeric: true, sensitivity: 'base' });

const compare: Record<FolderSort, (a: Folder, b: Folder) => number> = {
  created_desc: (a, b) => b.createdAt.localeCompare(a.createdAt) || b.id - a.id,
  created_asc: (a, b) => a.createdAt.localeCompare(b.createdAt) || a.id - b.id,
  name_asc: (a, b) => nameCollator.compare(a.name, b.name) || a.id - b.id,
  name_desc: (a, b) => nameCollator.compare(b.name, a.name) || b.id - a.id,
  size_desc: (a, b) => b.sizeBytes - a.sizeBytes || b.id - a.id,
  size_asc: (a, b) => a.sizeBytes - b.sizeBytes || a.id - b.id,
};

export const sortFolders = (folders: Folder[], sort: FolderSort): Folder[] => [...folders].sort(compare[sort]);
