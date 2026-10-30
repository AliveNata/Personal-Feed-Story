import React, { useCallback, useEffect, useState } from 'react';
import { Platform, PlatformSessionStatus } from '../types';
import {
  ApiError,
  deletePlatformSession,
  fetchPlatformSessions,
  formatRelativeTime,
  savePlatformSession,
} from '../utils/api';
import { Panel, focusRing, primaryButton, secondaryButton } from './ui';

interface PlatformGuide {
  label: string;
  site: string;
  documentRow: string;
  mustContain: string;
  extraTip: string;
}

const GUIDES: Record<Platform, PlatformGuide> = {
  instagram: {
    label: 'Instagram',
    site: 'https://www.instagram.com',
    documentRow: 'www.instagram.com',
    mustContain: 'sessionid',
    extraTip: 'Pakai akun cadangan khusus scraping. Kalau akun itu kena batas, akun utama lu tetap aman.',
  },
  tiktok: {
    label: 'TikTok',
    site: 'https://www.tiktok.com',
    documentRow: 'www.tiktok.com',
    mustContain: 'sessionid',
    extraTip: 'Setelah ganti cookie, feed TikTok yang pernah dibuka tetap ada di cache.',
  },
};

const SOURCE_TEXT: Record<PlatformSessionStatus['source'], string> = {
  web: 'Dipakai: cookie dari halaman ini',
  env: 'Dipakai: dari backend/.env (atau file session)',
  none: 'Belum ada session',
};

const minutes = (seconds: number) => Math.max(1, Math.ceil(seconds / 60));

const Guide: React.FC<{ guide: PlatformGuide; isOpen: boolean }> = ({ guide, isOpen }) => (
  <details open={isOpen} className="mt-4 rounded-md border border-gray-200 bg-gray-50 p-4">
    <summary className={`cursor-pointer font-semibold text-gray-900 ${focusRing}`}>
      Cara ambil cookie {guide.label}
    </summary>
    <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-gray-800">
      <li>
        Di PC, buka{' '}
        <a href={guide.site} target="_blank" rel="noopener noreferrer" className={`underline ${focusRing}`}>
          {guide.site.replace('https://', '')}
        </a>{' '}
        pakai Chrome atau Edge, lalu login.
      </li>
      <li>
        Tekan <kbd className="rounded border border-gray-300 bg-white px-1">F12</kbd> buat buka DevTools, lalu
        pilih tab <span className="font-semibold">Network</span>.
      </li>
      <li>
        Tekan <kbd className="rounded border border-gray-300 bg-white px-1">F5</kbd> buat refresh halaman.
      </li>
      <li>
        Di daftar request, klik baris paling atas yang namanya{' '}
        <code className="rounded bg-white px-1">{guide.documentRow}</code> (kolom Type: document).
      </li>
      <li>
        Di panel kanan buka <span className="font-semibold">Headers</span>, turun ke bagian{' '}
        <span className="font-semibold">Request Headers</span>, cari baris{' '}
        <code className="rounded bg-white px-1">cookie</code>, klik kanan nilainya, pilih{' '}
        <span className="font-semibold">Copy value</span>.
      </li>
      <li>Tempel di kolom di atas, lalu klik Simpan dan cek.</li>
    </ol>
    <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-gray-700">
      <li>
        Ambil dari <span className="font-semibold">Request Headers</span>, bukan{' '}
        <code className="rounded bg-white px-1">set-cookie</code> di Response Headers. Isinya harus ada{' '}
        <code className="rounded bg-white px-1">{guide.mustContain}=</code>.
      </li>
      <li>Jangan klik Logout di browser itu, logout bikin cookie-nya mati.</li>
      <li>Cookie ini sama berharganya dengan password. Jangan dikirim ke siapa pun.</li>
      <li>{guide.extraTip}</li>
    </ul>
  </details>
);

const SessionCard: React.FC<{
  status: PlatformSessionStatus;
  onChange: (status: PlatformSessionStatus) => void;
}> = ({ status, onChange }) => {
  const guide = GUIDES[status.platform];
  const [cookie, setCookie] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSaving(true);
    setMessage(null);
    try {
      const saved = await savePlatformSession(status.platform, cookie);
      onChange(saved);
      // The cookie is a credential: do not keep it on screen once it is stored.
      setCookie('');
      setMessage({
        tone: 'ok',
        text: saved.verified
          ? `Session tersimpan dan valid${saved.accountName ? ` untuk ${saved.accountName}` : ''}. Langsung dipakai tanpa restart.`
          : `Session tersimpan, tapi ${guide.label} lagi menahan request jadi belum bisa dicek. Dipakai mulai sekarang.`,
      });
    } catch (caught) {
      console.error('savePlatformSession failed', caught);
      setMessage({
        tone: 'error',
        text: caught instanceof ApiError ? caught.message : 'Gagal menyimpan session',
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemove = async () => {
    setMessage(null);
    try {
      onChange(await deletePlatformSession(status.platform));
      setMessage({ tone: 'ok', text: 'Cookie dari halaman ini dihapus, kembali pakai yang di .env.' });
    } catch (caught) {
      console.error('deletePlatformSession failed', caught);
      setMessage({
        tone: 'error',
        text: caught instanceof ApiError ? caught.message : 'Gagal menghapus session',
      });
    }
  };

  const inputId = `cookie-${status.platform}`;

  return (
    <section aria-labelledby={`${inputId}-title`} className="rounded-lg bg-white p-5 shadow-md">
      <h2 id={`${inputId}-title`} className="text-xl font-bold text-gray-900">
        {guide.label}
      </h2>
      <dl className="mt-2 space-y-1 text-sm text-gray-800">
        <div>
          <dt className="sr-only">Status</dt>
          <dd className="font-semibold">{SOURCE_TEXT[status.source]}</dd>
        </div>
        {status.accountName && (
          <div className="flex gap-1">
            <dt>Akun:</dt>
            <dd className="font-semibold">
              {status.accountName}
              {status.source === 'web' && !status.verified ? ' (belum terverifikasi)' : ''}
            </dd>
          </div>
        )}
        {status.updatedAt && (
          <div className="flex gap-1">
            <dt>Dipasang:</dt>
            <dd>{formatRelativeTime(status.updatedAt)}</dd>
          </div>
        )}
      </dl>
      {status.replaceReason && (
        <p role="alert" className="mt-3 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          <span className="font-semibold">Cookie ini perlu diganti.</span> {status.replaceReason}
          {status.problemSince ? ` (sejak ${formatRelativeTime(status.problemSince)})` : ''}. Login ulang di
          browser, lalu tempel cookie baru di bawah.
        </p>
      )}
      {status.waitSeconds > 0 && (
        <p
          role="status"
          className="mt-3 rounded-md border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900"
        >
          Request ke {guide.label} lagi ditahan app sekitar {minutes(status.waitSeconds)} menit karena tadi
          dibatasi. Cookie baru yang valid langsung menghapus jeda ini.
        </p>
      )}

      <form onSubmit={handleSave} className="mt-4">
        <label htmlFor={inputId} className="mb-1 block text-sm font-semibold text-gray-700">
          Tempel header Cookie {guide.label}
        </label>
        <textarea
          id={inputId}
          value={cookie}
          onChange={(event) => setCookie(event.target.value)}
          rows={4}
          required
          autoComplete="off"
          spellCheck={false}
          placeholder={`csrftoken=...; ${guide.mustContain}=...; ...`}
          className={`w-full rounded-md border border-gray-300 p-3 font-mono text-xs text-gray-900 ${focusRing}`}
        />
        <div className="mt-2 flex flex-wrap gap-2">
          <button type="submit" className={primaryButton} disabled={isSaving || cookie.trim().length < 10}>
            {isSaving ? 'Mengecek...' : 'Simpan dan cek'}
          </button>
          {status.source === 'web' && (
            <button type="button" className={secondaryButton} onClick={() => void handleRemove()}>
              Hapus, pakai yang di .env
            </button>
          )}
        </div>
        {message && (
          <p
            role={message.tone === 'error' ? 'alert' : 'status'}
            className={`mt-2 text-sm ${message.tone === 'error' ? 'text-red-700' : 'text-gray-800'}`}
          >
            {message.text}
          </p>
        )}
      </form>

      <Guide guide={guide} isOpen={status.source === 'none'} />
    </section>
  );
};

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; sessions: PlatformSessionStatus[] };

const SessionPage: React.FC = () => {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  const load = useCallback(() => {
    setState({ status: 'loading' });
    fetchPlatformSessions()
      .then((sessions) => setState({ status: 'ready', sessions }))
      .catch((caught: unknown) => {
        console.error('fetchPlatformSessions failed', caught);
        setState({
          status: 'error',
          message: caught instanceof ApiError ? caught.message : 'Gagal memuat status session',
        });
      });
  }, []);

  useEffect(load, [load]);

  const replace = (updated: PlatformSessionStatus) =>
    setState((previous) =>
      previous.status === 'ready'
        ? {
            status: 'ready',
            sessions: previous.sessions.map((item) => (item.platform === updated.platform ? updated : item)),
          }
        : previous,
    );

  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold">Session</h1>
      <p className="mb-6 max-w-2xl text-sm text-gray-700">
        Cookie akun yang dipakai app buat scraping dan download. Disimpan terenkripsi di server dan tidak
        pernah ditampilkan lagi. Cookie dari sini menggantikan yang di <code>backend/.env</code>.
      </p>
      {state.status === 'loading' && (
        <Panel role="status">
          <p>Memuat status session...</p>
        </Panel>
      )}
      {state.status === 'error' && (
        <Panel role="alert" tone="error">
          <p className="mb-4 text-gray-800">{state.message}</p>
          <button type="button" className={primaryButton} onClick={load}>
            Coba lagi
          </button>
        </Panel>
      )}
      {state.status === 'ready' && (
        <div className="grid gap-6 lg:grid-cols-2">
          {state.sessions.map((session) => (
            <SessionCard key={session.platform} status={session} onChange={replace} />
          ))}
        </div>
      )}
    </div>
  );
};

export default SessionPage;
