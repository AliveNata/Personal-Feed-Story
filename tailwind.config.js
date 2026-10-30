/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      keyframes: {
        'seek-flash': {
          '0%': { opacity: '0' },
          '15%': { opacity: '1' },
          '70%': { opacity: '1' },
          '100%': { opacity: '0' },
        },
        // One full turn when the password visibility icon swaps, so the change is noticeable.
        'spin-once': {
          from: { transform: 'rotate(-180deg) scale(0.6)', opacity: '0' },
          to: { transform: 'rotate(0deg) scale(1)', opacity: '1' },
        },
      },
      animation: {
        'spin-once': 'spin-once 320ms ease-out',
        // Seek feedback on a video: visible long enough to read, then gone.
        'seek-flash': 'seek-flash 700ms ease-out forwards',
      },
    },
  },
  plugins: [],
};
