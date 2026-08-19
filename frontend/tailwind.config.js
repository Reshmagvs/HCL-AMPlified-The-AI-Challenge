/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Deep navy ground, one warm accent. Everything else is a tint of these.
        ink: {
          950: '#060a16',
          900: '#0a1022',
          850: '#0e162c',
          800: '#121c37',
          700: '#1b2748',
          600: '#26355e',
          500: '#37497a',
        },
        ember: {
          400: '#ffb454',
          500: '#f59331',
          600: '#d97614',
        },
        mist: {
          100: '#e8ecf7',
          300: '#aeb9d6',
          500: '#7784a6',
        },
        signal: {
          ok: '#4ade80',
          warn: '#fbbf24',
          bad: '#f87171',
        },
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Consolas', 'ui-monospace', 'monospace'],
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'field-fill': {
          '0%': { backgroundColor: 'rgba(245,147,49,0.28)' },
          '100%': { backgroundColor: 'rgba(245,147,49,0)' },
        },
        'pulse-ring': {
          '0%': { transform: 'scale(0.95)', opacity: '0.7' },
          '70%': { transform: 'scale(1.25)', opacity: '0' },
          '100%': { transform: 'scale(1.25)', opacity: '0' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.28s ease-out both',
        'field-fill': 'field-fill 1.1s ease-out both',
        'pulse-ring': 'pulse-ring 1.8s cubic-bezier(0,0,0.2,1) infinite',
      },
    },
  },
  plugins: [],
}
