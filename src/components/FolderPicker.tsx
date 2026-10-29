import React, { useId, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, FolderPlus } from 'lucide-react';
import { Folder } from '../types';
import { focusRing } from './ui';

interface FolderPickerProps {
  id: string;
  value: string;
  folders: Folder[];
  onChange: (value: string) => void;
}

type Option = { kind: 'existing'; folder: Folder } | { kind: 'new'; name: string };

const normalize = (value: string) => value.trim().replace(/\s+/g, ' ').toLowerCase();

const folderMeta = (folder: Folder) => `${folder.photoCount} foto, ${folder.videoCount} video`;

/**
 * Type a new folder name or pick an existing one. Replaces the native datalist, which looks
 * different in every browser and cannot show folder details. ARIA combobox pattern.
 */
const FolderPicker: React.FC<FolderPickerProps> = ({ id, value, folders, onChange }) => {
  const listId = useId();
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const options = useMemo<Option[]>(() => {
    const query = normalize(value);
    const matches = folders
      .filter((folder) => normalize(folder.name).includes(query))
      .map((folder): Option => ({ kind: 'existing', folder }));
    const exact = folders.some((folder) => normalize(folder.name) === query);
    // Folder names are matched case insensitively by the backend, so "liburan" reuses "Liburan".
    return query && !exact ? [{ kind: 'new', name: value.trim() }, ...matches] : matches;
  }, [folders, value]);

  const choose = (option: Option) => {
    onChange(option.kind === 'existing' ? option.folder.name : option.name);
    setIsOpen(false);
    inputRef.current?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!isOpen) {
        setIsOpen(true);
        setActiveIndex(0);
        return;
      }
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((current) => (current + step + options.length) % Math.max(options.length, 1));
    } else if (event.key === 'Enter' && isOpen && options[activeIndex]) {
      // Enter picks the highlighted folder, the next Enter submits the form.
      event.preventDefault();
      choose(options[activeIndex]);
    } else if (event.key === 'Escape' && isOpen) {
      event.preventDefault();
      setIsOpen(false);
    }
  };

  const selectedKey = normalize(value);
  const showList = isOpen && options.length > 0;

  return (
    <div className="relative">
      <input
        ref={inputRef}
        id={id}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList ? `${listId}-${activeIndex}` : undefined}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setIsOpen(true);
          setActiveIndex(0);
        }}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setIsOpen(false)}
        onKeyDown={handleKeyDown}
        placeholder="Ketik nama folder baru, atau pilih yang sudah ada"
        maxLength={100}
        autoComplete="off"
        required
        className={`w-full min-h-[44px] rounded-md border border-gray-300 pl-3 pr-11 text-gray-900 ${focusRing}`}
      />
      <button
        type="button"
        tabIndex={-1}
        aria-label={isOpen ? 'Tutup daftar folder' : 'Buka daftar folder'}
        // mousedown, not click: keeps focus in the input so onBlur does not close the list first.
        onMouseDown={(event) => {
          event.preventDefault();
          setIsOpen((open) => !open);
          inputRef.current?.focus();
        }}
        className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-gray-700"
      >
        <ChevronDown
          aria-hidden="true"
          className={`h-5 w-5 transition-transform ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {showList && (
        // Opens upward: the download bar sits at the bottom of the screen.
        <ul
          id={listId}
          role="listbox"
          aria-label="Folder"
          className="absolute bottom-full left-0 right-0 z-30 mb-2 max-h-64 overflow-y-auto rounded-md border border-gray-300 bg-white py-1 shadow-lg"
        >
          {options.map((option, index) => {
            const isActive = index === activeIndex;
            const isSelected = option.kind === 'existing' && normalize(option.folder.name) === selectedKey;
            return (
              <li
                key={option.kind === 'existing' ? option.folder.id : 'new'}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={isSelected}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(option);
                }}
                onMouseEnter={() => setActiveIndex(index)}
                className={`flex min-h-[44px] cursor-pointer items-center gap-3 px-3 py-2 ${
                  isActive ? 'bg-gray-100' : ''
                }`}
              >
                {option.kind === 'new' ? (
                  <>
                    <FolderPlus aria-hidden="true" className="h-4 w-4 shrink-0 text-gray-700" />
                    <span className="min-w-0 truncate text-gray-900">
                      Buat folder baru <span className="font-semibold">"{option.name}"</span>
                    </span>
                  </>
                ) : (
                  <>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-gray-900">{option.folder.name}</span>
                      <span className="block text-xs text-gray-700">{folderMeta(option.folder)}</span>
                    </span>
                    {isSelected && <Check aria-hidden="true" className="h-4 w-4 shrink-0 text-gray-900" />}
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default FolderPicker;
