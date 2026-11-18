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
  retranslateWithClaude,
  translateSubtitles,
} from '../utils/api';
import { SubtitleChoice, readSubtitleChoice, saveSubtitleChoice } from '../utils/viewerPrefs';

const POLL_MS = 2000;

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
  progress: number | null;
  // A saved translation being made again with Claude.
  isRedoing: boolean;
}

export interface ClaudeRedo {
  isWorking: boolean;
  progress: number | null;
  onRedo: () => void;
}

export interface SubtitleControls {
  options: SubtitleOption[];
  choice: SubtitleChoice;
  onChoose: (choice: SubtitleChoice) => void;
  lines: SubtitleLine[] | null;
  // Offered for a Groq translation while ANTHROPIC_API_KEY is set, null otherwise.
  claude: ClaudeRedo | null;
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
  // Kept with who made them: a translation redone by Claude is fetched again.
  const [tracks, setTracks] = useState<
    Partial<Record<SubtitleTrackName, { translator: string | null; lines: SubtitleLine[] }>>
  >({});
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

  const progressOf = useCallback(
    (track: SubtitleTrackName): number | null =>
      info?.tracks.find((entry) => entry.track === track)?.progress ?? null,
    [info],
  );
  const translatorOf = useCallback(
    (track: SubtitleTrackName): string | null =>
      info?.tracks.find((entry) => entry.track === track)?.translator ?? null,
    [info],
  );
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
  const shownTranslator = shownTrack ? translatorOf(shownTrack) : null;
  const cached = shownTrack ? tracks[shownTrack] : undefined;
  const needsFetch =
    shownTrack !== null &&
    shownState === 'ready' &&
    (cached === undefined || cached.translator !== shownTranslator);
  useEffect(() => {
    if (!needsFetch || shownTrack === null) return;
    let isCurrent = true;
    fetchSubtitleTrack(itemId, shownTrack)
      .then(
        (track) =>
          isCurrent &&
          setTracks((previous) => ({
            ...previous,
            [shownTrack]: { translator: shownTranslator, lines: track.lines },
          })),
      )
      .catch((caught: unknown) => console.error('fetchSubtitleTrack failed', caught));
    return () => {
      isCurrent = false;
    };
  }, [itemId, shownTrack, shownTranslator, needsFetch]);

  const redoWithClaude = useCallback(
    (target: SubtitleTarget) => {
      setError(null);
      retranslateWithClaude(itemId, target)
        .then(setInfo)
        .catch((caught: unknown) => {
          console.error('retranslateWithClaude failed', caught);
          setError(caught instanceof Error ? caught.message : 'Gagal minta Claude');
        });
    },
    [itemId],
  );

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
      { choice: 'off', label: 'Mati', state: 'ready', progress: null, isRedoing: false },
      {
        choice: 'original',
        label: `Asli (${languageName(language)})`,
        state: 'ready',
        progress: null,
        isRedoing: false,
      },
    ];
    const translations: [SubtitleTarget, string][] = [
      ['en', 'English'],
      ['id', 'Indonesia'],
    ];
    for (const [target, label] of translations) {
      if (language !== target) {
        const state = stateOf(target);
        options.push({
          choice: target,
          label,
          state,
          progress: progressOf(target),
          isRedoing: isBusy(state) && translatorOf(target) !== null,
        });
      }
    }
    const shownChoice: SubtitleChoice = choice === language ? 'original' : choice;
    let claude: ClaudeRedo | null = null;
    if (info?.claudeAvailable && (shownTrack === 'en' || shownTrack === 'id')) {
      const target = shownTrack;
      const state = stateOf(target);
      const translator = translatorOf(target);
      const isWorking = isBusy(state) && translator !== null;
      if (isWorking || (state === 'ready' && translator !== 'claude')) {
        claude = { isWorking, progress: progressOf(target), onRedo: () => redoWithClaude(target) };
      }
    }
    return {
      options,
      choice: shownChoice,
      onChoose: choose,
      lines: shownTrack ? (tracks[shownTrack]?.lines ?? null) : null,
      claude,
    };
  }, [
    originalState,
    language,
    stateOf,
    progressOf,
    translatorOf,
    choice,
    choose,
    shownTrack,
    tracks,
    info,
    redoWithClaude,
  ]);

  const originalError = info?.tracks.find((entry) => entry.track === 'original')?.error ?? null;
  const shownError = shownTrack
    ? (info?.tracks.find((entry) => entry.track === shownTrack)?.error ?? null)
    : null;

  return {
    // Null for photos and until the first answer arrives.
    originalState: isVideo && info ? originalState : null,
    originalProgress: progressOf('original'),
    error: error ?? originalError ?? shownError,
    generate,
    controls,
  };
};
