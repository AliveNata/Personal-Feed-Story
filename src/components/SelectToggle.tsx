import React from 'react';
import { Check } from 'lucide-react';

interface SelectToggleProps {
  isSelected: boolean;
  label: string;
  onToggle: () => void;
}

/** Checkbox overlay on a card. 44px hit area so it works on touch. */
const SelectToggle: React.FC<SelectToggleProps> = ({ isSelected, label, onToggle }) => (
  <label className="absolute top-0 left-0 flex h-11 w-11 cursor-pointer items-center justify-center">
    <input
      type="checkbox"
      checked={isSelected}
      onChange={onToggle}
      aria-label={label}
      className="peer sr-only"
    />
    <span
      aria-hidden="true"
      className={`flex h-7 w-7 items-center justify-center rounded-md border-2 shadow peer-focus-visible:ring-2 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-gray-900 ${
        isSelected ? 'border-gray-900 bg-gray-900 text-white' : 'border-white bg-black/40 text-transparent'
      }`}
    >
      <Check className="h-5 w-5" strokeWidth={3} />
    </span>
  </label>
);

export default SelectToggle;
