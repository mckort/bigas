/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0f1419',
        surface: '#15202b',
        border: '#38444d',
        muted: '#8b98a5',
        accent: '#1d9bf0',
        text: '#e7e9ea',
      },
    },
  },
  plugins: [],
}
