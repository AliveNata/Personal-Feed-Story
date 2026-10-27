import React, { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { focusRing } from './ui';

interface PasswordInputProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
}

const PasswordInput: React.FC<PasswordInputProps> = ({ id, value, onChange, autoComplete }) => {
  const [isVisible, setIsVisible] = useState(false);
  // Skip the spin on first render, only animate when the user toggles.
  const [hasToggled, setHasToggled] = useState(false);

  const toggle = () => {
    setIsVisible((previous) => !previous);
    setHasToggled(true);
  };

  const Icon = isVisible ? EyeOff : Eye;

  return (
    <div className="relative">
      <input
        id={id}
        type={isVisible ? 'text' : 'password'}
        autoComplete={autoComplete}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`w-full min-h-[44px] pl-3 pr-12 border border-gray-300 rounded-md text-gray-900 ${focusRing}`}
        required
      />
      <button
        type="button"
        onClick={toggle}
        aria-label={isVisible ? 'Sembunyikan password' : 'Tampilkan password'}
        aria-pressed={isVisible}
        aria-controls={id}
        className={`absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-md text-gray-700 hover:text-gray-900 ${focusRing}`}
      >
        <Icon
          key={String(isVisible)}
          aria-hidden="true"
          className={`h-5 w-5 ${hasToggled ? 'motion-safe:animate-spin-once' : ''}`}
        />
      </button>
    </div>
  );
};

export default PasswordInput;
