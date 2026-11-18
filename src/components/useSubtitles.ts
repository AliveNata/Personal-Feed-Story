import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  SubtitleInfo,
  SubtitleLine,
  SubtitleState,
  SubtitleTarget,
  SubtitleTrackName,
} from '../types';
import {
  editSubtitleLine,
  fetchSubtitleInfo,
  fetchSubtitleTrack,
  generateSubtitles,
  retranslateSubtitles,
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

const TRACK_LABELS: Record<SubtitleTarget, string> = { en: 'English', id: 'Indonesia' };

export interface SubtitleOption {
  choice: SubtitleChoice;
  label: string;
  state: SubtitleState;
  progress: number | null;
  // A saved translation being made again.
  isRedoing: boolean;
}

export interface SubtitleAction {
  key: string;
  label: string;
  title: string;
  isWorking: boolean;
  onSelect: () => void;
}

export interface SubtitleControls {
  options: SubtitleOption[];
  choice: SubtitleChoice;
  onChoose: (choice: SubtitleChoice) => void;
  lines: SubtitleLine[] | null;
  // Edit, translate again, make it more natural with Claude: for the track on screen.
  actions: SubtitleAction[];
}

export interface CachedTrack {
  lines: SubtitleLine[];
  edited: number[];
}

export interface SubtitleEditor {
  track: SubtitleTrackName;
  label: string;
  // Null while loading.
  current: CachedTrack | null;
  // The spoken lines, shown beside a translation as reference. Null for the original track.
  original: CachedTrack | null;
  saveLine: (index: number, text: string) => Promise<void>;
  close: () => void;
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
  const [tracks, setTracks] = useState<Partial<Record<SubtitleTrackName, CachedTrack>>>({});
  const [editorTrack, setEditorTrack] = useState<SubtitleTrackName | null>(null);
  const requested = useRef(new Set<SubtitleTarget>());
  const previousStates = useRef<Partial<Record<SubtitleTrackName, SubtitleState>>>({});

  useEffect(() => {
    setInfo(null);
    setTracks({});
    setError(null);
    setEditorTrack(null);
    requested.current = new Set();
    previousStates.current = {};
    if (!isVideo) return;
    let isCurrent = true;
    fetchSubtitleInfo(itemId)
      .then((next) => isCurrent && setInfo(next))
      .catch((caught: unknown) => console.error('fetchSubtitleInfo failed', caught));
    return () => {
      isCurrent = false;
    };
  }, [itemId, isVideo]);

  // A track that just finished (made again, for instance) is fetched anew.
  useEffect(() => {
    if (!info) return;
    const finished = info.tracks.filter(
      (entry) => entry.state === 'ready' && isBusy(previousStates.current[entry.track]),
    );
    previousStates.current = Object.fromEntries(
      info.tracks.map((entry) => [entry.track, entry.state]),
    );
    if (finished.length) {
      setTracks((previous) => {
        const next = { ...previous };
        finished.forEach((entry) => delete next[entry.track]);
        return next;
      });
    }
  }, [info]);

  const entryOf = useCallback(
    (track: SubtitleTrackName) => info?.tracks.find((entry) => entry.track === track),
    [info],
  );
  const stateOf = useCallback(
    (track: SubtitleTrackName): SubtitleState => entryOf(track)?.state ?? 'none',
    [entryOf],
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

  // Tracks to have in hand: the one on screen, and while editing a translation its original.
  const wanted = new Set<SubtitleTrackName>();
  if (shownTrack) wanted.add(shownTrack);
  if (editorTrack) wanted.add(editorTrack).add('original');
  // A file is on the server once ready, and while a saved translation is being made again.
  const hasFile = (name: SubtitleTrackName): boolean =>
    stateOf(name) === 'ready' || (isBusy(stateOf(name)) && Boolean(entryOf(name)?.translator));
  const toFetchKey = [...wanted]
    .filter((name) => tracks[name] === undefined && hasFile(name))
    .join(',');

  useEffect(() => {
    if (!toFetchKey) return;
    let isCurrent = true;
    for (const name of toFetchKey.split(',') as SubtitleTrackName[]) {
      fetchSubtitleTrack(itemId, name)
        .then(
          (track) =>
            isCurrent &&
            setTracks((previous) => ({
              ...previous,
              [name]: { lines: track.lines, edited: track.edited },
            })),
        )
        .catch((caught: unknown) => console.error('fetchSubtitleTrack failed', caught));
    }
    return () => {
      isCurrent = false;
    };
  }, [itemId, toFetchKey]);

  const redo = useCallback(
    (target: SubtitleTarget, withClaude: boolean) => {
      setError(null);
      (withClaude ? retranslateWithClaude : retranslateSubtitles)(itemId, target)
        .then(setInfo)
        .catch((caught: unknown) => {
          console.error('retranslate failed', caught);
          setError(caught instanceof Error ? caught.message : 'Gagal minta terjemahan ulang');
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

  const saveLine = useCallback(
    async (track: SubtitleTrackName, index: number, text: string) => {
      const saved = await editSubtitleLine(itemId, track, index, text);
      setTracks((previous) => ({
        ...previous,
        [track]: { lines: saved.lines, edited: saved.edited },
      }));
    },
    [itemId],
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
    for (const target of ['en', 'id'] as const) {
      if (language !== target) {
        const entry = entryOf(target);
        options.push({
          choice: target,
          label: TRACK_LABELS[target],
          state: entry?.state ?? 'none',
          progress: entry?.progress ?? null,
          isRedoing: isBusy(entry?.state) && (entry?.translator ?? null) !== null,
        });
      }
    }

    const actions: SubtitleAction[] = [];
    if (shownTrack && tracks[shownTrack]) {
      actions.push({
        key: 'edit',
        label: 'Edit subtitle',
        title: 'Benerin teks subtitle baris per baris, tersimpan di server',
        isWorking: false,
        onSelect: () => setEditorTrack(shownTrack),
      });
    }
    if (shownTrack === 'en' || shownTrack === 'id') {
      const target = shownTrack;
      const entry = entryOf(target);
      const translator = entry?.translator ?? null;
      const isRedoing = isBusy(entry?.state) && translator !== null;
      const progress = entry?.progress ?? null;
      const working = progress === null ? 'nunggu antrean…' : `${progress}%`;
      if (isRedoing) {
        actions.push({
          key: 'redoing',
          label: `Lagi diterjemahin ulang ${working}`,
          title: 'Subtitle lama tetap tampil sampai yang baru selesai',
          isWorking: true,
          onSelect: () => undefined,
        });
      } else if (entry?.state === 'ready') {
        if (translator !== 'claude') {
          actions.push({
            key: 'redo',
            label: 'Terjemahin ulang',
            title: 'Terjemahin lagi baris yang belum diedit, editan kamu tetap',
            isWorking: false,
            onSelect: () => redo(target, false),
          });
        }
        if (info?.claudeAvailable && translator !== 'claude') {
          actions.push({
            key: 'claude',
            label: 'Bikin lebih santai (Claude)',
            title: 'Terjemahin ulang pakai Claude biar lebih natural, editan kamu tetap',
            isWorking: false,
            onSelect: () => redo(target, true),
          });
        }
      }
    }

    return {
      options,
      choice: choice === language ? 'original' : choice,
      onChoose: choose,
      lines: shownTrack ? (tracks[shownTrack]?.lines ?? null) : null,
      actions,
    };
  }, [originalState, language, entryOf, choice, choose, shownTrack, tracks, info, redo]);

  const editor = useMemo((): SubtitleEditor | null => {
    if (!editorTrack) return null;
    return {
      track: editorTrack,
      label:
        editorTrack === 'original' ? `Asli (${languageName(language)})` : TRACK_LABELS[editorTrack],
      current: tracks[editorTrack] ?? null,
      original: editorTrack === 'original' ? null : (tracks.original ?? null),
      saveLine: (index, text) => saveLine(editorTrack, index, text),
      close: () => setEditorTrack(null),
    };
  }, [editorTrack, language, tracks, saveLine]);

  const originalError = entryOf('original')?.error ?? null;
  const shownError = shownTrack ? (entryOf(shownTrack)?.error ?? null) : null;

  return {
    // Null for photos and until the first answer arrives.
    originalState: isVideo && info ? originalState : null,
    originalProgress: entryOf('original')?.progress ?? null,
    error: error ?? originalError ?? shownError,
    generate,
    controls,
    editor,
  };
};
