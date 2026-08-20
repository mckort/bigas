/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        'bigas-blue': '#73cdfb',
        'bigas-black': '#000000',
        'bigas-white': '#ffffff',
        bg: '#ffffff',
        surface: '#f8fbff',
        border: 'rgba(0, 0, 0, 0.1)',
        muted: 'rgba(0, 0, 0, 0.62)',
        accent: '#73cdfb',
        text: '#000000',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
      },
      boxShadow: {
        soft: '0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04)',
        card: '0 4px 16px rgba(0, 0, 0, 0.06)',
        input: '0 2px 12px rgba(0, 0, 0, 0.08)',
      },
    },
  },
  plugins: [],
}
