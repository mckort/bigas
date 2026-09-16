import { useEffect } from 'react'
import ThemeToggle from './ThemeToggle'
import { applyLandingSeo } from '../lib/landingSeo'

const GITHUB_REPO_URL = 'https://github.com/mckort/bigas'
const GITHUB_FORK_URL = 'https://github.com/mckort/bigas/fork'
const QUICKSTART_DOC_URL =
  'https://github.com/mckort/bigas/blob/main/docs/tutorial-set-up-a-virtual-ai-team.md'
const X_URL = 'https://x.com/bigasmyaiteam'

const SURFACES = [
  {
    title: 'Chat',
    path: '/',
    summary: 'Your control plane',
    body: 'Chief of Staff by default, or any specialist directly. Agents reason, use tools, and take action — file a card, review a PR, draft copy — instead of handing you a checklist.',
  },
  {
    title: 'Board',
    path: '/board',
    summary: 'Where work ships',
    body: 'Native Kanban with Jira-like workflows, releases, and a human-gated AI pipeline: research → plan → implement. Jira is optional if you already run a board elsewhere.',
  },
  {
    title: 'Objectives',
    path: '/objectives',
    summary: 'The quarter’s scoreboard',
    body: 'Name the aim; Key Results hold the numbers. Board cards link to a KR so shipping work and moving a metric are the same story.',
  },
]

const SPECIALISTS = [
  {
    name: 'Chief of Staff',
    role: 'Default chat agent — coordinates specialists and takes action rather than delegating back to you.',
  },
  {
    name: 'Marketing',
    role: 'GA4, paid ads, trends, and cross-platform insights; creates tracked follow-up work.',
  },
  {
    name: 'Product',
    role: 'Planning, board and Jira workflows, release notes, and stakeholder communication.',
  },
  {
    name: 'CTO',
    role: 'Code review, architecture, deployment debugging, and engineering operations.',
  },
  {
    name: 'CFO',
    role: 'AI and cloud spend, usage analysis, and efficiency — numbers first.',
  },
  {
    name: 'DevOps',
    role: 'Deployments, site health, incident response, and CI/CD safety.',
  },
]

const WORKFLOW_STEPS = [
  {
    title: 'Set the quarter',
    body: 'Open Objectives, define Key Results, and link board cards to the metrics you care about.',
  },
  {
    title: 'Ship on the board',
    body: 'Drag cards through your workflow. Assign releases, merge PRs, and let Product post progress when work lands in Done.',
  },
  {
    title: 'Steer in chat',
    body: 'Ask specialists to research, plan, implement, review, or report — humans approve; agents execute what you put in front of them.',
  },
]

const FAQ_ITEMS = [
  {
    q: 'Is bigas.me a signup page?',
    a: 'No. This host is one running instance of Bigas — a demo you can use if you have access. The product is open source on GitHub; fork it and run your own in minutes.',
  },
  {
    q: 'What do I need to run Bigas locally?',
    a: 'An LLM API key (Gemini or OpenAI). The setup wizard can configure in-memory chat and dev auth so you skip Firebase and Google Cloud for a first look.',
  },
  {
    q: 'Do I need Jira?',
    a: 'No. The built-in board is the default. Connect Jira only if you want to keep an existing Jira board in sync.',
  },
  {
    q: 'How is this different from a generic AI chat?',
    a: 'Specialists share your Objectives, board, and connected tools. They file tickets, review PRs, run reports, and post updates — not one-off answers in a blank thread.',
  },
]

function GitHubIcon({ className = 'w-[18px] h-[18px]' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .3a12 12 0 00-3.79 23.4c.6.11.82-.26.82-.58v-2.02c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.33-1.76-1.33-1.76-1.09-.74.08-.73.08-.73 1.2.09 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.5 1 .11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 016 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.62-5.49 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.82.58A12 12 0 0012 .3z" />
    </svg>
  )
}

function SectionHeading({ id, eyebrow, title, lead }) {
  return (
    <header className="max-w-2xl mx-auto text-center mb-10 sm:mb-12 px-1">
      {eyebrow ? (
        <p className="text-xs font-semibold uppercase tracking-wider text-brand mb-2">{eyebrow}</p>
      ) : null}
      <h2 id={id} className="text-2xl sm:text-3xl font-bold tracking-tight text-balance">
        {title}
      </h2>
      {lead ? <p className="mt-3 text-muted text-sm sm:text-base leading-relaxed">{lead}</p> : null}
    </header>
  )
}

function LandingHeader({ onSignIn }) {
  return (
    <header className="header-bar">
      <div className="max-w-5xl mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-3">
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
        <nav
          className="flex flex-wrap items-center justify-end gap-1 sm:gap-2 text-sm"
          aria-label="Primary"
        >
          <a
            href={GITHUB_REPO_URL}
            className="btn-ghost px-3 py-2 min-h-[44px] gap-1.5"
            target="_blank"
            rel="noopener noreferrer"
          >
            <GitHubIcon className="w-4 h-4" />
            <span className="hidden sm:inline">GitHub</span>
          </a>
          <button type="button" className="btn-ghost px-3 py-2 min-h-[44px]" onClick={onSignIn}>
            Sign in
          </button>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  )
}

export default function Landing({ onSignIn }) {
  useEffect(() => applyLandingSeo(), [])

  function handleSignInClick(e) {
    e.preventDefault()
    onSignIn?.()
  }

  return (
    <div className="min-h-screen bg-bg flex flex-col">
      <LandingHeader onSignIn={handleSignInClick} />

      <main className="flex-1">
        {/* Hero */}
        <section className="px-4 pt-12 pb-16 sm:pt-16 sm:pb-20 border-b border-border">
          <div className="max-w-3xl mx-auto text-center">
            <img
              src="/bigas-logo.png"
              alt="Bigas logo"
              className="h-16 w-16 sm:h-20 sm:w-20 rounded-xl object-cover mx-auto mb-6 shadow-soft"
              width={80}
              height={80}
            />
            <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-balance">
              A virtual AI team for solo founders
            </h1>
            <p className="mt-4 text-base sm:text-lg text-muted leading-relaxed max-w-xl mx-auto">
              <strong className="font-semibold text-text">Bigas</strong> (Latin for{' '}
              <em>team</em>) is open source. Chat, board, and objectives — marketing, product,
              engineering, and finance without hiring a staff.
            </p>
            <p className="mt-3 text-sm text-muted max-w-lg mx-auto">
              This site is one running instance, not a signup. Run your own copy from GitHub.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row gap-3 justify-center items-stretch sm:items-center">
              <a
                href={GITHUB_FORK_URL}
                className="btn-accent rounded-lg px-5 py-3 min-h-[48px] font-semibold shadow-soft"
                target="_blank"
                rel="noopener noreferrer"
              >
                Fork &amp; run in 5 minutes
              </a>
              <a
                href={GITHUB_REPO_URL}
                className="btn-secondary rounded-lg px-5 py-3 min-h-[48px] gap-2 font-semibold"
                target="_blank"
                rel="noopener noreferrer"
              >
                <GitHubIcon />
                Star on GitHub
              </a>
              <button
                type="button"
                className="btn-secondary rounded-lg px-5 py-3 min-h-[48px] font-semibold"
                onClick={handleSignInClick}
              >
                Sign in
              </button>
            </div>
          </div>
        </section>

        {/* Problem */}
        <section className="px-4 py-14 sm:py-16 bg-surface/40" aria-labelledby="problem-heading">
          <div className="max-w-3xl mx-auto">
            <SectionHeading
              id="problem-heading"
              eyebrow="The problem"
              title="You are product, marketing, and engineering at once"
              lead="Bigas is for the founder who keeps the roadmap, the ads, and the deploy pipeline in one head — on one product or a whole portfolio."
            />
            <ul className="space-y-4 text-sm sm:text-base leading-relaxed text-text/90 max-w-2xl mx-auto">
              <li className="flex gap-3">
                <span className="text-brand font-bold shrink-0" aria-hidden="true">
                  →
                </span>
                <span>
                  Context fragments across chat apps, spreadsheets, and issue trackers — nothing
                  ties the quarter’s goals to what actually ships.
                </span>
              </li>
              <li className="flex gap-3">
                <span className="text-brand font-bold shrink-0" aria-hidden="true">
                  →
                </span>
                <span>
                  Generic AI assistants answer once and forget. You still copy outcomes into Jira,
                  GitHub, and analytics tools by hand.
                </span>
              </li>
              <li className="flex gap-3">
                <span className="text-brand font-bold shrink-0" aria-hidden="true">
                  →
                </span>
                <span>
                  Bigas gives you one virtual HQ: objectives for aim, a board for work, chat for
                  specialists who can act on your stack.
                </span>
              </li>
            </ul>
          </div>
        </section>

        {/* Three surfaces */}
        <section className="px-4 py-14 sm:py-16" aria-labelledby="surfaces-heading">
          <div className="max-w-5xl mx-auto">
            <SectionHeading
              id="surfaces-heading"
              eyebrow="Three surfaces"
              title="One story from OKR to shipped work"
              lead="Objectives, board, and chat share the same tickets and metrics."
            />
            <ul className="grid gap-4 sm:grid-cols-3">
              {SURFACES.map((surface) => (
                <li key={surface.title} className="card p-5 sm:p-6 flex flex-col h-full">
                  <p className="text-xs font-medium text-muted uppercase tracking-wide">
                    {surface.path}
                  </p>
                  <h3 className="text-lg font-semibold mt-1">{surface.title}</h3>
                  <p className="text-sm text-brand font-medium mt-0.5">{surface.summary}</p>
                  <p className="mt-3 text-sm text-muted leading-relaxed flex-1">{surface.body}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Specialists */}
        <section
          className="px-4 py-14 sm:py-16 bg-surface/40 border-y border-border"
          aria-labelledby="team-heading"
        >
          <div className="max-w-5xl mx-auto">
            <SectionHeading
              id="team-heading"
              eyebrow="Specialist team"
              title="Six roles, one reasoning engine"
              lead="Specialists join when their expertise helps — no rigid handoff rules."
            />
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {SPECIALISTS.map((spec) => (
                <li key={spec.name} className="card p-4 sm:p-5">
                  <h3 className="font-semibold text-sm sm:text-base">{spec.name}</h3>
                  <p className="mt-1.5 text-sm text-muted leading-relaxed">{spec.role}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* How work moves */}
        <section className="px-4 py-14 sm:py-16" aria-labelledby="workflow-heading">
          <div className="max-w-3xl mx-auto">
            <SectionHeading
              id="workflow-heading"
              eyebrow="How work moves"
              title="Humans decide. Agents execute."
              lead="Cards do not auto-advance, and agents do not start work because a Key Result is off track — you still drag."
            />
            <ol className="space-y-6 max-w-2xl mx-auto">
              {WORKFLOW_STEPS.map((step, index) => (
                <li key={step.title} className="flex gap-4">
                  <span
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-muted text-accent text-sm font-bold"
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <div>
                    <h3 className="font-semibold">{step.title}</h3>
                    <p className="mt-1 text-sm text-muted leading-relaxed">{step.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Quickstart */}
        <section
          className="px-4 py-14 sm:py-16 bg-surface/40 border-t border-border"
          aria-labelledby="quickstart-heading"
        >
          <div className="max-w-3xl mx-auto">
            <SectionHeading
              id="quickstart-heading"
              eyebrow="Quickstart"
              title="MVP in under five minutes"
              lead="Only an LLM API key required — no Google Cloud or Firebase for local try-out."
            />
            <div className="card p-4 sm:p-6 overflow-x-auto">
              <pre className="text-xs sm:text-sm leading-relaxed text-text/90 whitespace-pre-wrap break-all sm:break-normal">
                <code>{`git clone https://github.com/mckort/bigas.git
cd bigas
python scripts/setup.py          # wizard → .env
docker compose up --build        # or: pip install -r requirements.txt && python run_core.py`}</code>
              </pre>
            </div>
            <p className="mt-4 text-sm text-muted text-center">
              Open{' '}
              <code className="text-xs bg-elevated border border-border rounded px-1.5 py-0.5">
                http://localhost:8080
              </code>
              , sign in with any email and dev token{' '}
              <code className="text-xs bg-elevated border border-border rounded px-1.5 py-0.5">
                bigas-dev-token
              </code>
              .
            </p>
            <div className="mt-6 flex flex-col sm:flex-row gap-3 justify-center">
              <a
                href={GITHUB_FORK_URL}
                className="btn-accent rounded-lg px-5 py-3 min-h-[48px] font-semibold text-center"
                target="_blank"
                rel="noopener noreferrer"
              >
                Fork on GitHub
              </a>
              <a
                href={QUICKSTART_DOC_URL}
                className="btn-secondary rounded-lg px-5 py-3 min-h-[48px] font-semibold text-center"
                target="_blank"
                rel="noopener noreferrer"
              >
                Read the tutorial
              </a>
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section className="px-4 py-14 sm:py-16" aria-labelledby="faq-heading">
          <div className="max-w-2xl mx-auto">
            <SectionHeading id="faq-heading" title="FAQ" />
            <div className="space-y-3">
              {FAQ_ITEMS.map((item) => (
                <details
                  key={item.q}
                  className="card group p-4 sm:p-5 [&_summary::-webkit-details-marker]:hidden"
                >
                  <summary className="font-medium cursor-pointer list-none flex justify-between gap-3 items-start">
                    <span>{item.q}</span>
                    <span
                      className="text-muted text-lg leading-none group-open:rotate-45 transition-transform shrink-0"
                      aria-hidden="true"
                    >
                      +
                    </span>
                  </summary>
                  <p className="mt-3 text-sm text-muted leading-relaxed">{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border px-4 py-8 sm:py-10 bg-elevated/50">
        <div className="max-w-5xl mx-auto flex flex-col sm:flex-row flex-wrap items-center justify-between gap-4 text-sm text-muted">
          <p className="text-center sm:text-left">
            Open source ·{' '}
            <a href={GITHUB_REPO_URL} className="hover:text-text underline-offset-2 hover:underline">
              mckort/bigas
            </a>
          </p>
          <nav className="flex flex-wrap items-center justify-center gap-x-4 gap-y-2" aria-label="Footer">
            <a href={X_URL} className="hover:text-text" target="_blank" rel="noopener noreferrer">
              @bigasmyaiteam
            </a>
            <a
              href={GITHUB_REPO_URL}
              className="hover:text-text inline-flex items-center gap-1"
              target="_blank"
              rel="noopener noreferrer"
            >
              <GitHubIcon className="w-4 h-4" />
              GitHub
            </a>
            <a href="/login" className="hover:text-text" onClick={handleSignInClick}>
              Sign in
            </a>
          </nav>
        </div>
      </footer>
    </div>
  )
}
