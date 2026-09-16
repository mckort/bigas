import { useEffect } from 'react'
import ThemeToggle from './ThemeToggle'

const GITHUB_REPO_URL = 'https://github.com/mckort/bigas'
const GITHUB_FORK_URL = 'https://github.com/mckort/bigas/fork'
const TUTORIAL_URL =
  'https://github.com/mckort/bigas/blob/main/docs/tutorial-set-up-a-virtual-ai-team.md'
const X_URL = 'https://x.com/bigasmyaiteam'

const NAV_LINKS = [
  { href: '#surfaces', label: 'Surfaces' },
  { href: '#team', label: 'Team' },
  { href: '#how-it-works', label: 'How it works' },
  { href: '#quickstart', label: 'Quickstart' },
  { href: '#faq', label: 'FAQ' },
]

const SURFACES = [
  {
    id: 'chat',
    title: 'Chat',
    path: '/',
    summary: 'Your control plane.',
    body: 'Chief of Staff by default, or any specialist directly. Agents reason, use tools, and take action — file a card, review a PR, draft copy — instead of handing you a checklist.',
  },
  {
    id: 'board',
    title: 'Board',
    path: '/board',
    summary: 'Where work ships.',
    body: 'Kanban tickets, columns, releases, and a human-gated AI workflow (research → plan → implement). Jira is optional if you already run a board you want to keep.',
  },
  {
    id: 'objectives',
    title: 'Objectives',
    path: '/objectives',
    summary: 'The quarter’s scoreboard.',
    body: 'Name the aim; Key Results hold the numbers; board cards link to a KR so shipping work and moving a metric are the same story.',
  },
]

const SPECIALISTS = [
  {
    name: 'Chief of Staff',
    role: 'Default chat agent',
    detail: 'Reasons through requests step by step, coordinates specialists, and takes action rather than asking you to do it.',
  },
  {
    name: 'Marketing',
    role: 'Senior Marketing Analyst',
    detail: 'GA4, paid ads (Google, Meta, LinkedIn, Reddit), trends, and cross-platform insights; creates tracked follow-up work.',
  },
  {
    name: 'Product',
    role: 'Product Manager',
    detail: 'Planning, board and Jira workflows, release notes, and stakeholder communication.',
  },
  {
    name: 'CTO',
    role: 'Engineering lead',
    detail: 'Code review, architecture, deployment debugging, and engineering operations.',
  },
  {
    name: 'CFO',
    role: 'Cost and usage',
    detail: 'AI and GCP spend, usage analysis, and efficiency — numbers first, with tracked follow-ups.',
  },
  {
    name: 'DevOps',
    role: 'Operations',
    detail: 'Deployments, site health, incident response, and CI/CD with safety-first reasoning.',
  },
]

const HOW_IT_WORKS = [
  {
    title: 'Set the quarter',
    body: 'Open Objectives — the shared scoreboard. Key Results are the numbers; board cards link to a KR so shipping and metrics stay aligned.',
  },
  {
    title: 'Ship during the week',
    body: 'Drag cards on the built-in board. Humans approve and sign off; agents research, plan, and implement the work you put in front of them.',
  },
  {
    title: 'Steer from chat',
    body: 'Ask in any specialist thread: draft release notes, review a PR, run analytics, or debug a failed deploy. Results land in chat (and optionally Discord).',
  },
]

const FAQ = [
  {
    q: 'Is bigas.me a signup page?',
    a: 'No. This host is one running instance of Bigas, not a SaaS signup. The product is open source on GitHub — fork it and run your own.',
  },
  {
    q: 'What do I need to try it locally?',
    a: 'An LLM API key is enough for the MVP. The setup wizard writes .env; Docker Compose or pip gets you to http://localhost:8080 in about five minutes.',
  },
  {
    q: 'Do I need Jira or Google Cloud?',
    a: 'No for local chat and the built-in board. Connect Jira, GA4, Firebase, and Cloud Run when you want those integrations in production.',
  },
  {
    q: 'Do agents auto-start work?',
    a: 'No. You drag cards and approve steps. Agents do not start tasks because a Key Result is off track — you stay in control.',
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
              Sign in
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
                A virtual AI team for solo founders
              </h1>
              <p className="mt-4 text-base sm:text-lg text-muted leading-relaxed max-w-2xl">
                <strong className="font-semibold text-text">Bigas</strong> (Latin for{' '}
                <em>team</em>) is your virtual HQ: chat with specialists, ship on a Kanban board, and
                track quarterly objectives — marketing, product, engineering, and finance without hiring
                a staff.
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
                Context lives in ten tabs: analytics, ads, GitHub, Jira, and the roadmap in your head.
              </li>
              <li className="card p-4 sm:p-5">
                Generic chatbots give checklists; they do not file tickets, review PRs, or draft release notes.
              </li>
              <li className="card p-4 sm:p-5">
                You need one place where goals, work, and conversation stay tied together — with you approving every move.
              </li>
            </ul>
          </div>
        </section>

        {/* Three surfaces */}
        <section className="py-12 sm:py-16 border-b border-border" aria-labelledby="surfaces-heading">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading
              id="surfaces"
              headingId="surfaces-heading"
              eyebrow="Three surfaces"
              title="Chat, board, and objectives — one story"
              lead="After you sign in to an instance, these are the main places work lives."
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
              title="Six agents, one coordinated staff"
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

        {/* How work moves */}
        <section className="py-12 sm:py-16 border-b border-border">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <SectionHeading
              id="how-it-works"
              eyebrow="How work moves"
              title="Goals → board → chat — you stay in control"
              lead="Humans decide. Agents execute the work you put in front of them. Cards do not auto-advance."
            />
            <ol className="mt-10 space-y-6 max-w-3xl">
              {HOW_IT_WORKS.map(({ title, body }, index) => (
                <li key={title} className="flex gap-4">
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-muted text-accent text-sm font-semibold border border-accent/20"
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <div>
                    <h3 className="font-semibold">{title}</h3>
                    <p className="mt-1 text-sm sm:text-base text-muted leading-relaxed">{body}</p>
                  </div>
                </li>
              ))}
            </ol>
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

# Open http://localhost:8080
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
              Sign in
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
