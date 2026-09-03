import { useEffect } from 'react'
import ThemeToggle from './ThemeToggle'

const GITHUB_REPO_URL = 'https://github.com/mckort/bigas'
const X_URL = 'https://x.com/bigasmyaiteam'

const SPECIALISTS = ['Chief of Staff', 'Marketing', 'Product', 'CTO', 'CFO', 'DevOps']

function GitHubIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .3a12 12 0 00-3.79 23.4c.6.11.82-.26.82-.58v-2.02c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.33-1.76-1.33-1.76-1.09-.74.08-.73.08-.73 1.2.09 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.5 1 .11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 016 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.62-5.49 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.82.58A12 12 0 0012 .3z" />
    </svg>
  )
}

export default function Landing({ onSignIn }) {
  useEffect(() => {
    const previousTitle = document.title
    document.title = 'Bigas — Virtual HQ for Solo Founders'
    return () => {
      document.title = previousTitle
    }
  }, [])

  return (
    <div className="min-h-screen bg-bg relative flex flex-col">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>

      <main className="flex-1 flex items-center justify-center px-4 py-16">
        <div className="w-full max-w-xl text-center">
          <img
            src="/bigas-logo.png"
            alt="Bigas"
            className="h-20 w-20 rounded-xl object-cover mx-auto mb-5 shadow-soft"
          />
          <h1 className="text-3xl sm:text-4xl font-bold tracking-tight">Bigas</h1>
          <p className="text-muted mt-3 text-base sm:text-lg">
            A virtual AI team for solo founders.
          </p>
          <p className="mt-5 text-sm sm:text-base leading-relaxed text-text/90">
            Bigas (Latin for <em>team</em>) is open source. Chat and a Kanban board are how work
            moves — marketing, product, engineering, and finance, without hiring a staff.
          </p>
          <p className="mt-3 text-sm text-muted">
            This site is one running instance, not a signup. The product lives on GitHub.
          </p>

          <div className="mt-8 flex flex-col sm:flex-row gap-3 justify-center">
            <a
              href={GITHUB_REPO_URL}
              className="btn-accent rounded-lg px-5 py-3 min-h-[48px] gap-2 font-semibold"
              target="_blank"
              rel="noopener noreferrer"
            >
              <GitHubIcon />
              Star on GitHub
            </a>
            <a
              href={GITHUB_REPO_URL}
              className="btn-secondary rounded-lg px-5 py-3 min-h-[48px] font-semibold"
              target="_blank"
              rel="noopener noreferrer"
            >
              View repository
            </a>
          </div>

          <ul className="mt-8 flex flex-wrap justify-center gap-2">
            {SPECIALISTS.map((name) => (
              <li
                key={name}
                className="text-xs text-muted bg-surface border border-border rounded-full px-2.5 py-1"
              >
                {name}
              </li>
            ))}
          </ul>
        </div>
      </main>

      <footer className="px-4 py-6 text-center text-sm text-muted">
        <a href={X_URL} className="hover:text-text" target="_blank" rel="noopener noreferrer">
          Follow @bigasmyaiteam
        </a>
        <span className="mx-2 opacity-40" aria-hidden="true">
          ·
        </span>
        <a
          href="/login"
          className="hover:text-text"
          onClick={(e) => {
            e.preventDefault()
            onSignIn?.()
          }}
        >
          Sign in
        </a>
      </footer>
    </div>
  )
}
