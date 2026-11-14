import { FolderCategory } from '../types';

export const CATEGORY_LABELS: Record<FolderCategory, string> = {
  lokal: 'Lokal',
  internasional: 'Internasional',
};

export const CATEGORIES: FolderCategory[] = ['lokal', 'internasional'];

const LAST_NEW_FOLDER_KEY = 'library.newFolderCategory';
const LIBRARY_TAB_KEY = 'library.categoryTab';

const read = (key: string): FolderCategory => {
  try {
    return window.localStorage.getItem(key) === 'internasional' ? 'internasional' : 'lokal';
  } catch (error) {
    console.warn(`could not read ${key} from localStorage`, error);
    return 'lokal';
  }
};

const write = (key: string, value: FolderCategory): void => {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    console.warn(`could not save ${key} to localStorage`, error);
  }
};

// A run of downloads from local accounts should not need a click per new folder.
export const readNewFolderCategory = (): FolderCategory => read(LAST_NEW_FOLDER_KEY);
export const saveNewFolderCategory = (category: FolderCategory): void => write(LAST_NEW_FOLDER_KEY, category);

export const readLibraryTab = (): FolderCategory => read(LIBRARY_TAB_KEY);
export const saveLibraryTab = (category: FolderCategory): void => write(LIBRARY_TAB_KEY, category);
