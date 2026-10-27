import React from 'react';

export const focusRing =
  'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-gray-900';

export const primaryButton = `min-h-[44px] px-4 rounded-md bg-gray-900 text-white text-sm font-semibold hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed ${focusRing}`;

export const secondaryButton = `min-h-[44px] px-4 rounded-md border border-gray-300 bg-white text-gray-800 text-sm font-medium hover:bg-gray-100 disabled:opacity-60 disabled:cursor-not-allowed ${focusRing}`;

export const dangerButton = `min-h-[44px] px-4 rounded-md border border-red-300 bg-white text-red-700 text-sm font-semibold hover:bg-red-50 disabled:opacity-60 disabled:cursor-not-allowed ${focusRing}`;

export const Panel: React.FC<{ children: React.ReactNode; role?: string; tone?: 'default' | 'error' }> = ({
  children,
  role,
  tone = 'default',
}) => (
  <div
    role={role}
    aria-live={role === 'status' ? 'polite' : undefined}
    className={`bg-white rounded-lg shadow-md p-6 text-gray-700 ${tone === 'error' ? 'border border-red-300' : ''}`}
  >
    {children}
  </div>
);

interface ConfirmDeleteProps {
  label: string;
  confirmText: string;
  onConfirm: () => Promise<void>;
}

/** Two step delete inline, instead of window.confirm, so it stays keyboard and screen reader friendly. */
export const ConfirmDelete: React.FC<ConfirmDeleteProps> = ({ label, confirmText, onConfirm }) => {
  const [isAsking, setIsAsking] = React.useState(false);
  const [isWorking, setIsWorking] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  if (!isAsking) {
    return (
      <button type="button" className={dangerButton} onClick={() => setIsAsking(true)}>
        {label}
      </button>
    );
  }

  const handleConfirm = async () => {
    setIsWorking(true);
    setError(null);
    try {
      await onConfirm();
    } catch (caught) {
      console.error('delete failed', caught);
      setError(caught instanceof Error ? caught.message : 'Gagal menghapus');
      setIsWorking(false);
    }
  };

  return (
    <div role="group" aria-label={confirmText} className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-gray-800">{confirmText}</span>
      <button type="button" className={dangerButton} onClick={handleConfirm} disabled={isWorking} autoFocus>
        {isWorking ? 'Menghapus...' : 'Ya, hapus'}
      </button>
      <button type="button" className={secondaryButton} onClick={() => setIsAsking(false)} disabled={isWorking}>
        Batal
      </button>
      {error && (
        <span role="alert" className="text-sm text-red-700">
          {error}
        </span>
      )}
    </div>
  );
};
