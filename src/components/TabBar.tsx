import React, { useRef } from 'react';
import { focusRing } from './ui';

export interface TabOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface TabBarProps<T extends string> {
  label: string;
  idPrefix: string;
  panelId: string;
  options: TabOption<T>[];
  active: T;
  onSelect: (value: T) => void;
  size?: 'large' | 'small';
}

/** ARIA tablist with arrow key navigation and roving tabindex. */
function TabBar<T extends string>({
  label,
  idPrefix,
  panelId,
  options,
  active,
  onSelect,
  size = 'large',
}: TabBarProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const handleKey = (event: React.KeyboardEvent, index: number) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
    const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length;
    onSelect(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className={size === 'large' ? 'flex border-b border-gray-300' : 'flex gap-2'}
    >
      {options.map((option, index) => {
        const isActive = option.value === active;
        const styles =
          size === 'large'
            ? `-mb-px min-h-[44px] px-5 text-base font-semibold border-b-2 ${
                isActive ? 'border-gray-900 text-gray-900' : 'border-transparent text-gray-700 hover:text-gray-900'
              }`
            : `min-h-[44px] rounded-md px-4 text-sm font-semibold ${
                isActive ? 'bg-gray-900 text-white' : 'bg-white text-gray-800 hover:bg-gray-100 border border-gray-300'
              }`;
        return (
          <button
            key={option.value}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-${option.value}`}
            aria-selected={isActive}
            aria-controls={panelId}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onSelect(option.value)}
            onKeyDown={(event) => handleKey(event, index)}
            className={`${styles} ${focusRing}`}
          >
            {option.label}
            {option.count !== undefined && <span className="ml-1 font-normal">({option.count})</span>}
          </button>
        );
      })}
    </div>
  );
}

export default TabBar;
