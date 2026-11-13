import { Fragment, useEffect } from 'react'
import ThemeToggle from './ThemeToggle'

const GITHUB_REPO_URL = 'https://github.com/mckort/bigas'
const GITHUB_FORK_URL = 'https://github.com/mckort/bigas/fork'
const TUTORIAL_URL =
  'https://github.com/mckort/bigas/blob/main/docs/tutorial-set-up-a-virtual-ai-team.md'
const X_URL = 'https://x.com/bigasmyaiteam'

const NAV_LINKS = [
  { href: '#how-it-works', label: 'How it works' },
  { href: '#modes', label: 'Dev → prod' },
  { href: '#surfaces', label: 'Surfaces' },
  { href: '#team', label: 'Team' },
  { href: '#quickstart', label: 'Quickstart' },
  { href: '#faq', label: 'FAQ' },
]

const HOW_IT_WORKS = [
  {
    title: 'Write a card',
    body: 'On the built-in, Jira-like board: a title, a short brief, maybe a screenshot. One board per GitHub repo.',
  },
  {
    title: 'The AI researches and plans; you approve',
    body: 'Product writes the description, the CTO writes the plan. Each waits in the cockpit for your approve or send back.',
  },
  {
    title: 'The AI implements; Bigas reviews the pull request',
    body: 'A Cursor agent opens the PR. Bigas reviews every PR and autofixes its own findings until it is ready to merge.',
  },
  {
    title: 'You ship',
    body: 'Deploy from the cockpit: straight from main while you build, or as a reviewed version once the project is in production.',
  },
]

const MODES = [
  {
    id: 'development',
    title: 'Development',
    tagline: 'While you build',
    points: [
      'Pull requests merge straight into main.',
      'Deploy ships main as it is, after a risk check.',
      'No versions, no extra branches.',
    ],
  },
  {
    id: 'production',
    title: 'Production',
    tagline: 'Once you have users',
    points: [
      'Work for each version lands on its own release branch (staging-0.2.0).',
      'Prepare deploy reviews the cut, merges it to main through a PR, deploys and tags v0.2.0.',
      'Once a version is merged to main, new cards move to the next version. Hotfixes still go straight to main.',
    ],
  },
]

const SURFACES = [
  {
    id: 'cockpit',
    title: 'Cockpit',
    path: '/',
    summary: 'What needs you.',
    body: 'Approvals and deploy confirms, what the AI is working on, one Ship card per project, and the week in numbers. Built for desktop and phone.',
  },
  {
    id: 'board',
    title: 'Board',
    path: '/board',
    summary: 'Where work ships.',
    body: 'Jira-like and built in: tickets, AI columns, releases. Connect real Jira only if you already run a board you want to keep.',
  },
  {
    id: 'chat',
    title: 'Chat',
    path: '/chat',
    summary: 'Ask the team.',
    body: 'Chief of Staff or any specialist. Agents use tools and take action — file a card, review a PR, run a deploy — instead of handing you a checklist.',
  },
]

const SPECIALISTS = [
  {
    name: 'Chief of Staff',
    role: 'Default chat agent',
    detail: 'Reasons through requests step by step, coordinates specialists, and takes action rather than asking you to do it.',
  },
  {
    name: 'Product',
    role: 'Product Manager',
    detail: 'Researches cards, writes release notes and the weekly update, and keeps the board moving.',
  },
  {
    name: 'CTO',
    role: 'Engineering lead',
    detail: 'Plans the work, reviews every pull request, and autofixes findings.',
  },
  {
    name: 'DevOps',
    role: 'Operations',
    detail: 'Deploys, prepares versioned releases, watches site health, and opens a fix when CI fails.',
  },
  {
    name: 'CFO',
    role: 'Cost and books',
    detail: 'AI and cloud spend plus month-to-date books, with anything unusual flagged.',
  },
  {
    name: 'Marketing',
    role: 'Optional',
    detail: 'GA4 and paid ads (Google, Meta, LinkedIn, Reddit) when you switch it on.',
  },
]

const FAQ = [
  {
    q: 'Is bigas.me a signup page?',
    a: 'No. This host is a private running instance, not a public account. Only the operator can sign in. The product is open source on GitHub — fork it and run your own.',
  },
  {
    q: 'What do I need to try it locally?',
    a: 'An LLM API key is enough. The setup wizard writes .env; Docker Compose or pip gets you to http://localhost:8080 in about five minutes, with the board and chat working.',
  },
  {
    q: 'What does it take to connect a repo?',
    a: 'A GitHub token, a Cursor API key, and Bigas running where GitHub can reach it (Cloud Run). One script adds the review workflow to your repo. A coding agent can do the whole setup with you: the repo has instructions for it.',
  },
  {
    q: 'How do I move a project from development to production?',
    a: 'Click Go to production on its Ship card. Bigas previews the steps, then adds the release workflows, creates version 0.1.0 and its staging branch, and sends new work there. Prepare deploy merges that branch to main.',
  },
  {
    q: 'Do I need Jira or Google Cloud?',
    a: 'Not for local use: the board is built in. Production runs on Google Cloud Run. Jira is optional.',
  },
  {
    q: 'Do agents auto-start work?',
    a: 'No. You drag cards and approve each step. Nothing reaches production without your yes.',
  },
]

function GitHubIcon({ className = 'w-[18px] h-[18px]' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .3a12 12 0 00-3.79 23.4c.6.11.82-.26.82-.58v-2.02c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.33-1.76-1.33-1.76-1.09-.74.08-.73.08-.73 1.2.09 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.5 1 .11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 016 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.62-5.49 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.82.58A12 12 0 0012 .3z" />
    </svg>
  )
}

function SectionHeading({ id, headingId, eyebrow, title, lead }) {
  return (
    <header id={id} className="scroll-mt-24 max-w-2xl">
      {eyebrow ? (
        <p className="text-xs font-semibold uppercase tracking-wider text-accent mb-2">{eyebrow}</p>
      ) : null}
      <h2
        id={headingId}
        className="text-2xl sm:text-3xl font-bold tracking-tight text-balance"
      >
        {title}
      </h2>
      {lead ? <p className="mt-3 text-muted text-base sm:text-lg leading-relaxed">{lead}</p> : null}
    </header>
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

  function handleSignIn(e) {
    if (onSignIn) {
      e.preventDefault()
      onSignIn()
    }
  }

  return (
    <div className="min-h-screen-safe bg-bg flex flex-col">
      <header className="header-bar">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-14 sm:h-16 flex items-center justify-between gap-3">
          <a href="/" className="flex items-center gap-2.5 min-w-0 shrink-0">
            <img
              src="/bigas-logo.png"
              alt=""
              className="h-9 w-9 rounded-lg object-cover shadow-soft"
              width={36}
              height={36}
            />
            <span className="font-semibold tracking-tight truncate">Bigas</span>
          </a>
          <nav className="hidden md:flex items-center gap-1 text-sm" aria-label="Page sections">
            {NAV_LINKS.map(({ href, label }) => (
              <a key={href} href={href} className="btn-ghost px-3 py-2 min-h-0 text-sm">
                {label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            <ThemeToggle />
            <a href="/login" className="btn-secondary text-sm px-3 sm:px-4 min-h-[40px]" onClick={handleSignIn}>
              <span className="sm:hidden">Sign in</span>
              <span className="hidden sm:inline">Sign in to this instance</span>
            </a>
          </div>
        </div>
      </header>

      <main className="flex-1">
        {/* Hero */}
        <section className="border-b border-border bg-gradient-to-b from-[var(--bigas-blue-soft)] to-transparent">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 py-12 sm:py-20">
            <div className="max-w-3xl">
              <p className="text-sm font-medium text-accent mb-3">Open source · fork and run</p>
              <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-balance leading-tight">
                From a card on the board to production, with an AI team
              </h1>
              <p className="mt-4 text-base sm:text-lg text-muted leading-relaxed max-w-2xl">
                <strong className="font-semibold text-text">Bigas</strong> (Latin for{' '}
                <em>team</em>) is a virtual HQ for solo founders. Write a card on the built-in board; the
                AI researches, plans and opens a reviewed pull request; you approve each step and ship.
                Straight from main while you build, as versioned releases once you have users.
              </p>
              <p className="mt-3 text-sm text-muted max-w-2xl">
                <strong className="font-medium text-text/90">bigas.me</strong> is one running instance,
                not a signup. Primary path: fork the repo and run locally in about five minutes.
              </p>
              <div className="mt-8 flex flex-col sm:flex-row flex-wrap gap-3">
                <a
                  href={GITHUB_FORK_URL}
                  className="btn-accent rounded-lg px-5 py-3 min-h-[48px] font-semibold justify-center"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Fork &amp; run in 5 minutes
                </a>
                <a
                  href={GITHUB_REPO_URL}
                  className="btn-secondary rounded-lg px-5 py-3 min-h-[48px] font-semibold gap-2 justify-center"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <GitHubIcon />
                  Star on GitHub
                </a>
                <a href="/login" className="btn-ghost rounded-lg px-5 py-3 min-h-[48px] font-semibold" onClick={handleSignIn}>
                  Sign in to this instance
                </a>
              </div>
              <nav
                className="mt-8 flex flex-wrap gap-2 md:hidden"
                aria-label="Jump to section"
              >
                {NAV_LINKS.map(({ href, label }) => (
                  <a
                    key={href}
                    href={href}
                    className="text-xs text-muted bg-surface border border-border rounded-full px-3 py-1.5 hover:text-text"
                  >
                    {label}
                  </a>
                ))}
              </nav>
              <figure className="mt-10">
                <img
                  src="/marketing/cockpit.png"
                  alt="Cockpit dashboard: this week’s numbers, a Ship card per project, and AI work in progress"
                  className="w-full h-auto rounded-xl border border-border shadow-soft"
                  width={838}
                  height={1024}
                  loading="lazy"
                  decoding="async"
                />
              </figure>
            </div>
          </div>
        </section>

        {/* Problem */}
        <section className="py-12 sm:py-16 border-b border-border">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 grid lg:grid-cols-2 gap-10 lg:gap-16 items-start">
            <SectionHeading
              eyebrow="The problem"
              title="You are product, marketing, and engineering at once"
              lead="Bigas is for the person running a product alone or across a portfolio — without a bench of specialists to delegate to."
            />
            <ul className="space-y-4 text-sm sm:text-base text-text/90 leading-relaxed">
              <li className="card p-4 sm:p-5">
                Context lives in ten tabs: GitHub, the issue tracker, deploys, costs, and the roadmap in your head.
              </li>
              <li className="card p-4 sm:p-5">
                Coding agents write code, but nobody reviews it, versions it, or ships it safely for you.
              </li>
              <li className="card p-4 sm:p-5">
                You need one place where every card goes from idea to production — with you approving every move.
              </li>
            </ul>
          </div>
        </section>

        {/* How it works */}
        <section className="py-12 sm:py-16 border-b border-border">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading
              id="how-it-works"
              eyebrow="How it works"
              title="One loop for every card"
              lead="Humans decide. Agents do the work you put in front of them. Cards never move on their own."
            />
            <ol className="mt-10 grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {HOW_IT_WORKS.map(({ title, body }, index) => (
                <li key={title} className="card p-5 flex flex-col gap-3">
                  <span
                    className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-muted text-accent text-sm font-semibold border border-accent/20"
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <h3 className="font-semibold">{title}</h3>
                  <p className="text-sm text-muted leading-relaxed">{body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Development and production */}
        <section className="py-12 sm:py-16 border-b border-border bg-surface/40" aria-labelledby="modes-heading">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading
              id="modes"
              headingId="modes-heading"
              eyebrow="Development → production"
              title="Start fast. Get strict when users arrive."
              lead="Every project runs in one of two modes. Switching is one button in the cockpit: Go to production."
            />
            <div className="mt-10 grid md:grid-cols-[1fr_auto_1fr] gap-4 items-stretch">
              {MODES.map(({ id, title, tagline, points }, i) => (
                <Fragment key={id}>
                  {i === 1 && (
                    <div className="hidden md:flex items-center justify-center text-accent text-sm font-semibold px-2">
                      <span className="rounded-full border border-accent/30 bg-accent-muted px-3 py-1.5 whitespace-nowrap">
                        Go to production →
                      </span>
                    </div>
                  )}
                  <article className={`card p-5 sm:p-6 ${id === 'production' ? 'border-accent/40' : ''}`}>
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted">{tagline}</p>
                    <h3 className="mt-1 text-xl font-semibold">{title}</h3>
                    <ul className="mt-4 space-y-2 text-sm text-muted leading-relaxed">
                      {points.map((point) => (
                        <li key={point} className="flex gap-2">
                          <span className="text-accent" aria-hidden="true">
                            ✓
                          </span>
                          <span>{point}</span>
                        </li>
                      ))}
                    </ul>
                  </article>
                </Fragment>
              ))}
            </div>
            <p className="mt-6 text-sm text-muted max-w-3xl">
              Go to production adds the release workflows to your repo, creates version 0.1.0 and its
              release branch, and sends new work there. Prepare deploy merges that branch to main. It shows
              every step before it runs.
            </p>
          </div>
        </section>

        {/* Three surfaces */}
        <section className="py-12 sm:py-16 border-b border-border" aria-labelledby="surfaces-heading">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading
              id="surfaces"
              headingId="surfaces-heading"
              eyebrow="Three surfaces"
              title="Cockpit, board and chat"
              lead="After you sign in, these are the places work lives. Objectives (OKRs) and marketing reports are optional."
            />
            <div className="mt-10 grid sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
              {SURFACES.map(({ id, title, path, summary, body }) => (
                <article key={id} className="card p-5 sm:p-6 flex flex-col h-full">
                  <p className="text-xs font-mono text-muted">{path}</p>
                  <h3 className="mt-2 text-xl font-semibold">
                    {title}
                  </h3>
                  <p className="text-sm font-medium text-accent mt-1">{summary}</p>
                  <p className="mt-3 text-sm text-muted leading-relaxed flex-1">{body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* Specialist team */}
        <section className="py-12 sm:py-16 border-b border-border bg-surface/40">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading
              id="team"
              eyebrow="Specialist team"
              title="A small staff that ships"
              lead="Each specialist keeps a thread so you can jump in without reconstructing context. Chief of Staff sees what you have connected."
            />
            <ul className="mt-10 grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {SPECIALISTS.map(({ name, role, detail }) => (
                <li key={name} className="card p-5">
                  <h3 className="font-semibold">{name}</h3>
                  <p className="text-xs text-accent mt-0.5">{role}</p>
                  <p className="mt-2 text-sm text-muted leading-relaxed">{detail}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Quickstart */}
        <section className="py-12 sm:py-16 border-b border-border bg-surface/40">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading
              id="quickstart"
              eyebrow="Quickstart"
              title="Fork and run locally in about five minutes"
              lead="Only an LLM API key required for the MVP — no Google Cloud or Firebase for local dev."
            />
            <div className="mt-8 flex flex-col lg:flex-row gap-6 lg:gap-10">
              <pre className="flex-1 text-xs sm:text-sm bg-elevated border border-border rounded-xl p-4 sm:p-5 overflow-x-auto leading-relaxed shadow-soft">
                <code>{`git clone https://github.com/mckort/bigas.git
cd bigas
python scripts/setup.py          # wizard → .env
docker compose up --build        # or: pip install -r requirements.txt && python run_core.py

# Open http://localhost:8080 → the cockpit
# Dev sign-in: any email + token bigas-dev-token`}</code>
              </pre>
              <div className="lg:w-72 shrink-0 flex flex-col gap-3">
                <a
                  href={GITHUB_FORK_URL}
                  className="btn-accent rounded-lg px-5 py-3 min-h-[48px] font-semibold text-center"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Fork on GitHub
                </a>
                <a
                  href={TUTORIAL_URL}
                  className="btn-secondary rounded-lg px-5 py-3 min-h-[48px] font-semibold text-center text-sm"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Step-by-step tutorial
                </a>
                <p className="text-xs text-muted leading-relaxed">
                  Build the UI once:{' '}
                  <code className="text-[0.85em] bg-surface px-1 rounded">cd frontend && npm run build</code>
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section className="py-12 sm:py-16">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading id="faq" eyebrow="FAQ" title="Common questions" />
            <div className="mt-8 space-y-3 max-w-3xl">
              {FAQ.map(({ q, a }) => (
                <details key={q} className="card group">
                  <summary className="cursor-pointer list-none px-4 sm:px-5 py-4 font-medium text-sm sm:text-base flex items-center justify-between gap-3">
                    <span>{q}</span>
                    <span
                      className="text-muted text-lg leading-none transition-transform group-open:rotate-45"
                      aria-hidden="true"
                    >
                      +
                    </span>
                  </summary>
                  <div className="px-4 sm:px-5 pb-4 pt-0 text-sm text-muted leading-relaxed border-t border-border/60">
                    {a}
                  </div>
                </details>
              ))}
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border bg-elevated/50">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-10 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6">
          <div className="flex items-center gap-3">
            <img src="/bigas-logo.png" alt="" className="h-8 w-8 rounded-md object-cover" width={32} height={32} />
            <div>
              <p className="font-semibold text-sm">Bigas</p>
              <p className="text-xs text-muted">Virtual HQ for solo founders</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-muted">
            <a href={GITHUB_REPO_URL} className="hover:text-text inline-flex items-center gap-1.5" target="_blank" rel="noopener noreferrer">
              <GitHubIcon className="w-4 h-4" />
              GitHub
            </a>
            <a href={X_URL} className="hover:text-text" target="_blank" rel="noopener noreferrer">
              @bigasmyaiteam
            </a>
            <a href="/login" className="hover:text-text" onClick={handleSignIn}>
              Sign in to this instance
            </a>
          </div>
        </div>
        <p className="text-center text-xs text-muted pb-6 px-4">
          Open source under{' '}
          <a href={`${GITHUB_REPO_URL}/blob/main/LICENSE`} className="underline hover:text-text" target="_blank" rel="noopener noreferrer">
            LICENSE
          </a>
          . This site is a demo instance — not a product signup.
        </p>
      </footer>
    </div>
  )
}
