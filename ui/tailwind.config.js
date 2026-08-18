/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          primary: 'rgb(var(--brand-primary) / <alpha-value>)',
          'primary-ink': 'rgb(var(--brand-primary-ink) / <alpha-value>)',
          'primary-ink-hover': 'rgb(var(--brand-primary-ink-hover) / <alpha-value>)',
          'primary-hover': 'rgb(var(--brand-primary-hover) / <alpha-value>)',
          'primary-soft': 'rgb(var(--brand-primary-soft) / <alpha-value>)',
          'on-primary': 'rgb(var(--brand-on-primary) / <alpha-value>)',
          secondary: 'rgb(var(--brand-secondary) / <alpha-value>)',
          'secondary-ink': 'rgb(var(--brand-secondary-ink) / <alpha-value>)',
          'secondary-ink-hover': 'rgb(var(--brand-secondary-ink-hover) / <alpha-value>)',
          'secondary-hover': 'rgb(var(--brand-secondary-hover) / <alpha-value>)',
          'secondary-soft': 'rgb(var(--brand-secondary-soft) / <alpha-value>)',
          'on-secondary': 'rgb(var(--brand-on-secondary) / <alpha-value>)',
          'menu-hover': 'rgb(var(--brand-menu-hover) / <alpha-value>)',
          'menu-hover-foreground': 'rgb(var(--brand-menu-hover-foreground) / <alpha-value>)',
        },
      },
    },
  },
  plugins: [],
}
