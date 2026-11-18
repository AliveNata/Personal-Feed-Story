import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  SubtitleInfo,
  SubtitleLine,
  SubtitleState,
  SubtitleTarget,
  SubtitleTrackName,
} from '../types';
import {
  fetchSubtitleInfo,
  fetchSubtitleTrack,
  generateSubtitles,
  translateSubtitles,
} from '../utils/api';
import { SubtitleChoice, readSubtitleChoice, saveSubtitleChoice } from '../utils/viewerPrefs';

const POLL_MS = 3000;

const LANGUAGE_NAMES: Record<string, string> = {
  en: 'Inggris', id: 'Indonesia', ms: 'Melayu', jw: 'Jawa', su: 'Sunda', ja: 'Jepang',
  ko: 'Korea', zh: 'Mandarin', th: 'Thai', vi: 'Vietnam', tl: 'Tagalog', hi: 'Hindi',
  ar: 'Arab', es: 'Spanyol', pt: 'Portugis', fr: 'Prancis', de: 'Jerman', it: 'Italia',
  nl: 'Belanda', ru: 'Rusia', tr: 'Turki', pl: 'Polandia', uk: 'Ukraina',
};

export const languageName = (code: string | null): string =>
  code ? (LANGUAGE_NAMES[code] ?? code.toUpperCase()) : 'tidak dikenal';

export interface SubtitleOption {
  choice: SubtitleChoice;
  label: string;
  state: SubtitleState;
}

export interface SubtitleControls {
  options: SubtitleOption[];
  choice: SubtitleChoice;
  onChoose: (choice: SubtitleChoice) => void;
  lines: SubtitleLine[] | null;
}

const isBusy = (state: SubtitleState | undefined): boolean =>
  state === 'queued' || state === 'running';

/**
 * Subtitle state for the video in the viewer. The original track is made only when asked
 * (Generate), a translation when picked in the CC menu. While anything is being made the state
 * is polled, and a finished track is fetched once and kept for this video.
 */
export const useSubtitles = (itemId: number, isVideo: boolean) => {
  const [info, setInfo] = useState<SubtitleInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState<SubtitleChoice>(readSubtitleChoice);
  const [tracks, setTracks] = useState<Partial<Record<SubtitleTrackName, SubtitleLine[]>>>({});
  const requested = useRef(new Set<SubtitleTarget>());

  useEffect(() => {
    setInfo(null);
    setTracks({});
    setError(null);
    requested.current = new Set();
    if (!isVideo) return;
    let isCurrent = true;
    fetchSubtitleInfo(itemId)
      .then((next) => isCurrent && setInfo(next))
      .catch((caught: unknown) => console.error('fetchSubtitleInfo failed', caught));
    return () => {
      isCurrent = false;
    };
  }, [itemId, isVideo]);

  const stateOf = useCallback(
    (track: SubtitleTrackName): SubtitleState =>
      info?.tracks.find((entry) => entry.track === track)?.state ?? 'none',
    [info],
  );
  const anyBusy = info?.tracks.some((entry) => isBusy(entry.state)) ?? false;

  useEffect(() => {
    if (!anyBusy) return;
    let isCurrent = true;
    const timer = window.setInterval(() => {
      fetchSubtitleInfo(itemId)
        .then((next) => isCurrent && setInfo(next))
        .catch((caught: unknown) => console.error('subtitle poll failed', caught));
    }, POLL_MS);
    return () => {
      isCurrent = false;
      window.clearInterval(timer);
    };
  }, [anyBusy, itemId]);

  // The same language as the speech needs no translation: show the original instead.
  const language = info?.language ?? null;
  const shownTrack: SubtitleTrackName | null =
    choice === 'off' ? null : choice === language ? 'original' : choice;

  const requestTranslation = useCallback(
    (target: SubtitleTarget) => {
      if (requested.current.has(target)) return;
      requested.current.add(target);
      translateSubtitles(itemId, target)
        .then(setInfo)
        .catch((caught: unknown) => {
          console.error('translateSubtitles failed', caught);
          setError(caught instanceof Error ? caught.message : 'Gagal minta terjemahan');
        });
    },
    [itemId],
  );

  // A saved English or Indonesia choice is translated as soon as the original exists.
  useEffect(() => {
    if (shownTrack === null || shownTrack === 'original') return;
    if (stateOf('original') === 'ready' && stateOf(shownTrack) === 'none') {
      requestTranslation(shownTrack);
    }
  }, [shownTrack, stateOf, requestTranslation]);

  const shownState = shownTrack ? stateOf(shownTrack) : 'none';
  useEffect(() => {
    if (shownTrack === null || shownState !== 'ready' || tracks[shownTrack]) return;
    let isCurrent = true;
    fetchSubtitleTrack(itemId, shownTrack)
      .then((track) => isCurrent && setTracks((previous) => ({ ...previous, [shownTrack]: track.lines })))
      .catch((caught: unknown) => console.error('fetchSubtitleTrack failed', caught));
    return () => {
      isCurrent = false;
    };
  }, [itemId, shownTrack, shownState, tracks]);

  const generate = useCallback(() => {
    setError(null);
    generateSubtitles(itemId)
      .then(setInfo)
      .catch((caught: unknown) => {
        console.error('generateSubtitles failed', caught);
        setError(caught instanceof Error ? caught.message : 'Gagal minta subtitle');
      });
  }, [itemId]);

  const choose = useCallback(
    (next: SubtitleChoice) => {
      setChoice(next);
      saveSubtitleChoice(next);
      // A failed translation is asked for again by picking it again.
      if ((next === 'en' || next === 'id') && next !== language && stateOf(next) === 'failed') {
        requested.current.delete(next);
        requestTranslation(next);
      }
    },
    [stateOf, language, requestTranslation],
  );

  const originalState = stateOf('original');
  const controls = useMemo((): SubtitleControls | null => {
    if (originalState !== 'ready') return null;
    const options: SubtitleOption[] = [
      { choice: 'off', label: 'Mati', state: 'ready' },
      { choice: 'original', label: `Asli (${languageName(language)})`, state: 'ready' },
    ];
    if (language !== 'en') options.push({ choice: 'en', label: 'English', state: stateOf('en') });
    if (language !== 'id') options.push({ choice: 'id', label: 'Indonesia', state: stateOf('id') });
    const shownChoice: SubtitleChoice = choice === language ? 'original' : choice;
    return {
      options,
      choice: shownChoice,
      onChoose: choose,
      lines: shownTrack ? (tracks[shownTrack] ?? null) : null,
    };
  }, [originalState, language, stateOf, choice, choose, shownTrack, tracks]);

  const originalError = info?.tracks.find((entry) => entry.track === 'original')?.error ?? null;
  const shownError = shownTrack
    ? (info?.tracks.find((entry) => entry.track === shownTrack)?.error ?? null)
    : null;

  return {
    // Null for photos and until the first answer arrives.
    originalState: isVideo && info ? originalState : null,
    error: error ?? originalError ?? shownError,
    generate,
    controls,
  };
};
