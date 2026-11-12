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

type IconTone = 'green' | 'red' | 'blue';

// Darker shades of each hue so the icon keeps a 4.5:1 contrast on the white button.
const ICON_TONES: Record<IconTone, string> = {
  green: 'text-green-700 hover:bg-green-50',
  red: 'text-red-700 hover:bg-red-50',
  blue: 'text-blue-700 hover:bg-blue-50',
};

interface IconActionProps {
  label: string;
  icon: React.ReactNode;
  tone: IconTone;
  onClick?: () => void;
  // Given, the action is a link (a download) instead of a button.
  href?: string;
}

/**
 * Round 44px icon button with its name in a tooltip on hover and keyboard focus. The name is
 * also the accessible label, so screen readers get the same words as the tooltip.
 */
export const IconAction: React.FC<IconActionProps> = ({ label, icon, tone, onClick, href }) => {
  const className = `flex h-11 w-11 items-center justify-center rounded-full border border-gray-300 bg-white shadow-sm ${ICON_TONES[tone]} ${focusRing}`;
  return (
    <span className="group relative inline-flex">
      {href ? (
        <a href={href} download aria-label={label} className={className}>
          {icon}
        </a>
      ) : (
        <button type="button" aria-label={label} onClick={onClick} className={className}>
          {icon}
        </button>
      )}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-900 px-2 py-1 text-xs font-medium text-white opacity-0 shadow transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 motion-reduce:transition-none"
      >
        {label}
      </span>
    </span>
  );
};

interface ConfirmDeleteProps {
  label: string;
  confirmText: string;
  onConfirm: () => Promise<void>;
  // Over a photo backdrop the question and error need light text.
  isOnDark?: boolean;
  // Shown instead of the text button before the question is asked.
  icon?: React.ReactNode;
}

/** Two step delete inline, instead of window.confirm, so it stays keyboard and screen reader friendly. */
export const ConfirmDelete: React.FC<ConfirmDeleteProps> = ({
  label,
  confirmText,
  onConfirm,
  isOnDark = false,
  icon,
}) => {
  const [isAsking, setIsAsking] = React.useState(false);
  const [isWorking, setIsWorking] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  if (!isAsking) {
    if (icon) return <IconAction label={label} icon={icon} tone="red" onClick={() => setIsAsking(true)} />;
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
      <span className={`text-sm ${isOnDark ? 'text-white' : 'text-gray-800'}`}>{confirmText}</span>
      <button type="button" className={dangerButton} onClick={handleConfirm} disabled={isWorking} autoFocus>
        {isWorking ? 'Menghapus...' : 'Ya, hapus'}
      </button>
      <button
        type="button"
        className={secondaryButton}
        onClick={() => setIsAsking(false)}
        disabled={isWorking}
      >
        Batal
      </button>
      {error && (
        <span role="alert" className={`text-sm ${isOnDark ? 'text-red-200' : 'text-red-700'}`}>
          {error}
        </span>
      )}
    </div>
  );
};
