/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Warm paper, warm ink, one clay accent. The whole palette is built to
        // feel like a well-made study notebook rather than a dashboard.
        paper: {
          50: '#FEFCF8', // raised surfaces / cards
          100: '#FBF8F2', // page
          200: '#F5F0E6', // sunken panels, table stripes
          300: '#EDE6D8', // hover
          400: '#E3DACA', // borders
          500: '#D5C9B4', // strong borders, dividers
        },
        ink: {
          900: '#2A251F', // headings
          700: '#463F36', // body
          500: '#6E655A', // secondary
          400: '#8B8175', // muted
          300: '#A9A093', // placeholder
        },
        clay: {
          50: '#FBF1ED',
          100: '#F4DDD4',
          300: '#D08C74',
          500: '#B0563A', // the accent
          600: '#98462D',
          700: '#7C3823',
        },
        sage: { 100: '#E7EDE4', 500: '#5B7A5A', 700: '#435C42' },
        amber: { 100: '#F7EBD6', 500: '#B08034', 700: '#8A6224' },
        rust: { 100: '#F6E2DE', 500: '#A8443A', 700: '#87342C' },
      },
      fontFamily: {
        // System stacks only: nothing is fetched, so the first paint is correct
        // even offline and the app never flashes an unstyled heading.
        serif: ['Iowan Old Style', 'Palatino Linotype', 'Palatino', 'Georgia', 'serif'],
        sans: ['Inter', 'Segoe UI', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Consolas', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgba(42,37,31,0.04), 0 4px 12px rgba(42,37,31,0.04)',
        lift: '0 2px 4px rgba(42,37,31,0.06), 0 12px 28px rgba(42,37,31,0.09)',
      },
      borderRadius: { xl: '0.875rem', '2xl': '1.125rem' },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(5px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'field-fill': {
          '0%': { backgroundColor: 'rgba(176,86,58,0.16)' },
          '100%': { backgroundColor: 'rgba(176,86,58,0)' },
        },
        nudge: {
          '0%,100%': { transform: 'translateX(0)' },
          '50%': { transform: 'translateX(3px)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.26s ease-out both',
        'field-fill': 'field-fill 1.4s ease-out both',
        nudge: 'nudge 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
