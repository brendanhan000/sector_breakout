/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Terminal palette. Flat, no gradients. Colour carries meaning only.
        term: {
          bg: '#0b0d10',
          panel: '#12151a',
          raised: '#171b22',
          border: '#232935',
          grid: '#1b2029',
          text: '#d6dae2',
          muted: '#79828f',
          dim: '#4c5563',
        },
        // Quadrant / state colours. Shared by the RRG and the state grid so a
        // green dot means the same thing in both places.
        sig: {
          up: '#2ec27e',
          upDim: '#1a6b48',
          down: '#e5484d',
          downDim: '#7c2427',
          warn: '#e2a03f',
          warnDim: '#6b4a17',
          info: '#3d8bfd',
          infoDim: '#1d4478',
          neutral: '#5a6472',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
    },
  },
  plugins: [],
};
