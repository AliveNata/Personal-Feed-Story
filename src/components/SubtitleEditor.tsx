import React, { useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { SubtitleEditor as EditorState } from './useSubtitles';

const formatTime = (seconds: number): string => {
  const whole = Math.max(Math.floor(seconds), 0);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = String(whole % 60).padStart(2, '0');
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${secs}` : `${minutes}:${secs}`;
};

type Filter = 'all' | 'untranslated';

interface SubtitleEditorProps {
  editor: EditorState;
  videoRef: React.RefObject<HTMLVideoElement>;
}

/**
 * Line by line editing of the subtitle on screen. Each saved line goes to the server at once.
 * For a translation the spoken line sits above it, and a filter lists only the lines that are
 * still word for word the original: the ones the translator skipped or refused.
 */
const SubtitleEditor: React.FC<SubtitleEditorProps> = ({ editor, videoRef }) => {
  const [filter, setFilter] = useState<Filter>('all');
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [now, setNow] = useState(0);
  const listRef = useRef<HTMLOListElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const hasScrolled = useRef(false);

  const current = editor.current;
  const lines = current?.lines ?? null;
  const originalLines = editor.original?.lines ?? null;
  const isTranslation = editor.track !== 'original';
  const edited = useMemo(() => new Set(current?.edited ?? []), [current]);

  const untranslated = useMemo(() => {
    if (!lines || !originalLines) return new Set<number>();
    return new Set(
      lines
        .map((line, index) => (line.text === originalLines[index]?.text && !edited.has(index) ? index : -1))
        .filter((index) => index >= 0),
    );
  }, [lines, originalLines, edited]);

  const shown = useMemo(() => {
    if (!lines) return [];
    const all = lines.map((line, index) => ({ line, index }));
    return filter === 'untranslated' ? all.filter(({ index }) => untranslated.has(index)) : all;
  }, [lines, filter, untranslated]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const sync = () => setNow(video.currentTime);
    sync();
    video.addEventListener('timeupdate', sync);
    return () => video.removeEventListener('timeupdate', sync);
  }, [videoRef]);

  const playing = lines?.findIndex((line) => line.start <= now && now < line.end) ?? -1;

  // Open at the line being played, once the lines are in.
  useEffect(() => {
    if (!lines || hasScrolled.current) return;
    hasScrolled.current = true;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-line="${Math.max(playing, 0)}"]`);
    row?.scrollIntoView({ block: 'center' });
  }, [lines, playing]);

  // Escape stops an edit, then closes the editor, and never closes the whole viewer.
  useEffect(() => {
    const dialog = sheetRef.current?.closest('dialog');
    if (!dialog) return;
    const onCancel = (event: Event) => {
      event.preventDefault();
      if (editing !== null) setEditing(null);
      else editor.close();
    };
    dialog.addEventListener('cancel', onCancel);
    return () => dialog.removeEventListener('cancel', onCancel);
  }, [editing, editor]);

  const jumpTo = (seconds: number) => {
    const video = videoRef.current;
    if (video) video.currentTime = seconds;
  };

  const startEdit = (index: number, text: string) => {
    videoRef.current?.pause();
    setEditing(index);
    setDraft(text);
    setSaveError(null);
  };

  const save = async () => {
    if (editing === null) return;
    const text = draft.trim();
    if (!text) {
      setSaveError('Teks nggak boleh kosong');
      return;
    }
    setIsSaving(true);
    setSaveError(null);
    try {
      await editor.saveLine(editing, text);
      setEditing(null);
    } catch (caught) {
      console.error('saving subtitle line failed', caught);
      setSaveError(caught instanceof Error ? caught.message : 'Gagal nyimpen');
    } finally {
      setIsSaving(false);
    }
  };

  const tab = (value: Filter, label: string) => (
    <button
      type="button"
      onClick={() => setFilter(value)}
      aria-pressed={filter === value}
      className={`rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 ${
        filter === value ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
      }`}
    >
      {label}
    </button>
  );

  return (
    <div
      ref={sheetRef}
      role="region"
      aria-label={`Edit subtitle ${editor.label}`}
      className="absolute inset-x-0 bottom-0 z-40 flex max-h-[70%] flex-col rounded-t-xl bg-white text-gray-900 shadow-2xl sm:inset-x-auto sm:right-4 sm:w-[28rem]"
    >
      <div className="flex items-center justify-between gap-2 border-b border-gray-200 px-4 py-3">
        <h2 className="text-base font-semibold">Edit subtitle: {editor.label}</h2>
        <button
          type="button"
          onClick={editor.close}
          aria-label="Tutup editor subtitle"
          className="flex h-9 w-9 items-center justify-center rounded-md text-gray-600 hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
        >
          <X className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>

      <div className="space-y-2 border-b border-gray-200 px-4 py-2">
        {isTranslation && (
          <div className="flex flex-wrap gap-2">
            {tab('all', `Semua (${lines?.length ?? 0})`)}
            {tab('untranslated', `Belum keterjemah (${originalLines ? untranslated.size : '…'})`)}
          </div>
        )}
        <p className="text-xs text-gray-500">Tap teks buat edit. Tap jam buat loncat ke bagian itu.</p>
      </div>

      {!lines ? (
        <p className="px-4 py-6 text-sm text-gray-500">Lagi ngambil subtitle…</p>
      ) : shown.length === 0 ? (
        <p className="px-4 py-6 text-sm text-gray-500">Semua baris udah keterjemah.</p>
      ) : (
        <ol ref={listRef} className="min-h-0 flex-1 divide-y divide-gray-100 overflow-y-auto overscroll-contain">
          {shown.map(({ line, index }) => {
            const isEditing = editing === index;
            return (
              <li
                key={index}
                data-line={index}
                className={`flex gap-3 px-4 py-2 ${index === playing ? 'bg-blue-50' : ''}`}
              >
                <button
                  type="button"
                  onClick={() => jumpTo(line.start)}
                  aria-label={`Loncat ke ${formatTime(line.start)}`}
                  className="h-fit shrink-0 rounded px-1 py-0.5 text-xs font-medium tabular-nums text-blue-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                >
                  {formatTime(line.start)}
                </button>
                <div className="min-w-0 flex-1">
                  {isTranslation && originalLines?.[index] && (
                    <p className="text-xs text-gray-500">{originalLines[index].text}</p>
                  )}
                  {isEditing ? (
                    <div className="mt-1 space-y-2">
                      <label className="sr-only" htmlFor={`subtitle-line-${index}`}>
                        Teks baris {formatTime(line.start)}
                      </label>
                      <textarea
                        id={`subtitle-line-${index}`}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' && !event.shiftKey) {
                            event.preventDefault();
                            void save();
                          }
                        }}
                        rows={2}
                        autoFocus
                        maxLength={500}
                        className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
                      />
                      {saveError && (
                        <p role="alert" className="text-xs text-red-700">
                          {saveError}
                        </p>
                      )}
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => void save()}
                          disabled={isSaving}
                          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                        >
                          {isSaving ? 'Nyimpen…' : 'Simpan'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditing(null)}
                          disabled={isSaving}
                          className="rounded-md px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                        >
                          Batal
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => startEdit(index, line.text)}
                      className="mt-0.5 block w-full rounded text-left text-sm hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                    >
                      {line.text}
                      {edited.has(index) && (
                        <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-600">
                          diedit
                        </span>
                      )}
                      {untranslated.has(index) && (
                        <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">
                          belum keterjemah
                        </span>
                      )}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
};

export default SubtitleEditor;
