import React from 'react';
import { Check } from 'lucide-react';

interface SelectToggleProps {
  isSelected: boolean;
  label: string;
  onToggle: () => void;
  isDisabled?: boolean;
}

/** Checkbox overlay on a card. 44px hit area so it works on touch. */
const SelectToggle: React.FC<SelectToggleProps> = ({ isSelected, label, onToggle, isDisabled = false }) => (
  <label
    className={`absolute top-0 left-0 flex h-11 w-11 items-center justify-center ${
      isDisabled ? 'cursor-not-allowed' : 'cursor-pointer'
    }`}
  >
    <input
      type="checkbox"
      checked={isDisabled || isSelected}
      onChange={onToggle}
      disabled={isDisabled}
      aria-label={label}
      className="peer sr-only"
    />
    <span
      aria-hidden="true"
      className={`flex h-7 w-7 items-center justify-center rounded-md border-2 shadow peer-focus-visible:ring-2 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-gray-900 ${
        isDisabled
          ? 'border-gray-400 bg-gray-300 text-gray-600'
          : isSelected
            ? 'border-gray-900 bg-gray-900 text-white'
            : 'border-white bg-black/40 text-transparent'
      }`}
    >
      <Check className="h-5 w-5" strokeWidth={3} />
    </span>
  </label>
);

export default SelectToggle;
