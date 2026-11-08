// Viewer settings kept per browser. Storage can be missing (private mode, blocked site data),
// so every read falls back to the default and a failed write only logs.

const REPEAT_KEY = 'viewer.repeatVideo';
const SLIDESHOW_SECONDS_KEY = 'viewer.slideshowSeconds';

export const SLIDESHOW_SPEEDS = [2, 3, 5, 10] as const;
export type SlideshowSeconds = (typeof SLIDESHOW_SPEEDS)[number];
const DEFAULT_SLIDESHOW_SECONDS: SlideshowSeconds = 3;

const read = (key: string): string | null => {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    console.warn(`could not read ${key} from localStorage`, error);
    return null;
  }
};

const write = (key: string, value: string): void => {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    console.warn(`could not save ${key} to localStorage`, error);
  }
};

export const readRepeatVideo = (): boolean => read(REPEAT_KEY) === 'true';
export const saveRepeatVideo = (repeat: boolean): void => write(REPEAT_KEY, String(repeat));

export const readSlideshowSeconds = (): SlideshowSeconds => {
  const stored = Number(read(SLIDESHOW_SECONDS_KEY));
  return SLIDESHOW_SPEEDS.find((speed) => speed === stored) ?? DEFAULT_SLIDESHOW_SECONDS;
};

export const saveSlideshowSeconds = (seconds: SlideshowSeconds): void =>
  write(SLIDESHOW_SECONDS_KEY, String(seconds));
