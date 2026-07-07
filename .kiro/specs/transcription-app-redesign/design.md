# Design Document

## Overview

This design transforms the existing single-page Deepgram/Khaya transcription demo into a routed, multi-page **React + Vite** application. It introduces React Router for client-side routing, lazy-loaded page components, a reusable component library, React context/hooks for state management, a Material Design 3 token layer over the existing Deepgram `dg-*` design system, light/dark theming, and an animation layer (Motion One / Framer Motion) with reduced-motion fallbacks.

The redesign replaces the existing vanilla-JS frontend entirely. **No backend endpoint changes are made.** The Deepgram and Khaya provider flows, JWT session auth (`getSessionToken` / `authenticatedFetch`), the LocalStorage history contract (`deepgram_transcription_history`, max 50 entries), `request_id` deep-linking, and metadata display are all preserved by extracting the current logic into reusable service modules and React hooks.

### Guiding Constraints

- **React + Vite** as the frontend framework. JSX components, React Router, React hooks.
- **No backend changes.** Endpoints remain: `POST /api/transcription`, `POST /api/khaya/transcription`, `GET /api/khaya/languages`, `GET /api/metadata`, `GET /api/session`. Requests flow through the Vite `/api` proxy to Express on `:8081`.
- **MD3 via CSS custom properties**, layered over existing `dg-*` variables. `@material/web` web components are NOT used (maintenance-mode).
- **Pinned exact dependency versions**, `async`/`await`, JSDoc where helpful.
- **Frontend directory** (`frontend/`) is rebuilt as a React Vite app, replacing the existing vanilla-JS submodule.

### Requirements Coverage Map

| Design Section | Requirements |
|----------------|--------------|
| Routing (React Router) | 2, 3.5, 18.1, 18.2 |
| Global navigation | 3 |
| Landing page | 4 |
| Transcribe workspace (upload/controls/status) | 5, 6, 7 |
| Transcript viewer + virtualization | 8, 18.3 |
| Results sidebar + export | 9 |
| Projects page | 10 |
| History page | 11 |
| About page | 12 |
| Error surfaces | 13, 5.3, 7.4 |
| Component library + design system | 14 |
| Theming | 15 |
| Animation/motion | 16, 3.6 |
| Accessibility | 17 |
| Performance (lazy-load, code split, defer) | 18 |
| Provider services (preserve behavior) | 1 |
| Data models (History/Projects) | 1.9–1.12, 10, 11 |
| Scope boundaries | 19 (no UI built for these surfaces) |

## Architecture

### High-Level Structure

The application is a client-side rendered SPA using React Router. A root `<App>` component hosts the persistent shell (navigation bar, theme toggle, footer, and a `<main>` outlet). React Router's `<Outlet>` swaps page components on navigation. Pages are loaded via `React.lazy()` for automatic code splitting.

```
┌─────────────────────────────────────────────┐
│  App Shell                                  │
│  ┌─────────────────────────────────────┐    │
│  │  NavBar (persistent)                │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │  <Outlet> (React Router)            │    │
│  │  ← lazy-loaded page components      │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │  Footer (persistent)               │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

### Layered Design

```
Presentation Layer     →  React components (pages + reusable UI)
State Layer            →  React Context + hooks (theme, auth, history, projects)
Services Layer         →  Pure JS modules (API calls, localStorage, export formatters)
Backend                →  Express API (unchanged, accessed via Vite proxy)
```

- **Presentation** — React page components and reusable UI components. Render JSX, wire events, delegate I/O to hooks/services.
- **State** — React Context providers (ThemeContext, AuthContext, HistoryContext, ProjectContext) and custom hooks for consuming them.
- **Services** — Pure JS modules that own network calls, persistence, and data transforms. Extracted from the existing `main.js` with behavior preserved.

### Folder Structure

```
frontend/
  index.html                 # minimal HTML entry (React root mount)
  vite.config.js             # Vite + React plugin, /api proxy
  package.json               # React, React Router, Framer Motion, etc.
  public/
    images/                  # Unsplash photos (optimized)
  src/
    main.jsx                 # React root render
    App.jsx                  # App shell: Router, NavBar, Outlet, Footer
    router.jsx               # Route definitions with React.lazy pages
    contexts/
      ThemeContext.jsx        # light/dark mode state + persistence
      AuthContext.jsx         # session token management
    hooks/
      useAuth.js             # authenticatedFetch hook
      useHistory.js          # transcription history CRUD hook
      useProjects.js         # projects CRUD hook
      useTheme.js            # theme consumer hook
      useTranscription.js    # transcription request + status hook
    services/
      session.js             # getSessionToken, authenticatedFetch
      transcription.js       # Deepgram + Khaya request builders
      metadata.js            # GET /api/metadata
      history-repo.js        # LocalStorage history CRUD (preserved)
      project-repo.js        # LocalStorage projects CRUD (new)
      export/
        index.js             # format dispatch
        txt.js  srt.js  vtt.js
        docx.js  pdf.js      # optional, lazy-loaded
    components/
      ui/                    # reusable primitives
        Button.jsx  Card.jsx  FormField.jsx  Tabs.jsx
        Table.jsx  Tooltip.jsx  Toast.jsx  Progress.jsx
        Skeleton.jsx  EmptyState.jsx
      layout/
        NavBar.jsx  Footer.jsx  ThemeToggle.jsx  Sidebar.jsx
      features/
        UploadZone.jsx  Waveform.jsx  AudioPlayer.jsx
        TranscriptViewer.jsx  VirtualList.jsx  ResultsSidebar.jsx
    pages/
      Landing.jsx  Transcribe.jsx  Projects.jsx
      History.jsx  About.jsx
      errors/
        NotFound.jsx  ProcessingError.jsx  UnsupportedFormat.jsx
    styles/
      tokens.css             # MD3 token layer
      theme-light.css  theme-dark.css
      base.css  components.css
    utils/
      format.js  validation.js  audio-formats.js  search.js
    assets/
      images/
        credits.js           # Unsplash attribution data
```

### Bundling and Code Splitting

Vite with `@vitejs/plugin-react` handles JSX transforms and HMR. React Router + `React.lazy()` produces automatic code-split chunks per page. Heavy optional libraries (waveform, DOCX/PDF) are imported lazily at point-of-use.

## Routing Design

### Approach

React Router v6 with `createBrowserRouter` and clean URLs. The Vite dev server's SPA fallback handles deep-link reloads. Production (Caddy) uses `try_files {path} /index.html`.

### Route Table

| Path | Component | Notes |
|------|-----------|-------|
| `/` | `Landing` | Req 4 |
| `/transcribe` | `Transcribe` | accepts `?request_id=` (Req 2.6, 1.11) |
| `/projects` | `Projects` | Req 10 |
| `/history` | `History` | Req 11 |
| `/about` | `About` | Req 12 |
| `*` | `NotFound` | Req 2.4, 13.1 |

### Router Definition

```jsx
// router.jsx
import { createBrowserRouter } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import App from './App';
import Loading from './components/ui/Skeleton';

const Landing = lazy(() => import('./pages/Landing'));
const Transcribe = lazy(() => import('./pages/Transcribe'));
const Projects = lazy(() => import('./pages/Projects'));
const History = lazy(() => import('./pages/History'));
const About = lazy(() => import('./pages/About'));
const NotFound = lazy(() => import('./pages/errors/NotFound'));

export const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Suspense fallback={<Loading />}><Landing /></Suspense> },
      { path: 'transcribe', element: <Suspense fallback={<Loading />}><Transcribe /></Suspense> },
      { path: 'projects', element: <Suspense fallback={<Loading />}><Projects /></Suspense> },
      { path: 'history', element: <Suspense fallback={<Loading />}><History /></Suspense> },
      { path: 'about', element: <Suspense fallback={<Loading />}><About /></Suspense> },
      { path: '*', element: <Suspense fallback={<Loading />}><NotFound /></Suspense> },
    ],
  },
]);
```

## Global Navigation

`NavBar.jsx` renders the persistent app bar using React Router's `<NavLink>` (auto-applies active class via `aria-current="page"`). Below the mobile breakpoint, links collapse behind a hamburger toggle with `aria-expanded`. Elevation uses `--md-sys-color-surface-container` tonal color.

## State Management

### ThemeContext

```jsx
// Provides: { theme, setTheme, toggleTheme }
// Persists to localStorage key 'theme'
// Applies class to <html> element: data-theme="light"|"dark"
// Honors prefers-color-scheme on first load
```

### AuthContext

```jsx
// Provides: { authenticatedFetch }
// Internally manages: getSessionToken, token cache, 401 handling
// Extracted from existing main.js logic (Req 1.7, 1.8)
```

### Custom Hooks

- `useHistory()` — wraps `history-repo.js` with React state; provides `entries`, `saveEntry`, `getById`, `clearAll`
- `useProjects()` — wraps `project-repo.js`; provides `projects`, `create`, `rename`, `delete`, `duplicate`, `search`, `sort`
- `useTranscription()` — manages request lifecycle: `idle → uploading → processing → complete | error`; calls transcription service; normalizes response

## Services and Data Models

### Services (pure JS, framework-agnostic)

Identical contracts to the vanilla design — these are plain JS modules consumed by hooks:

- `session.js` — `getSessionToken()`, `authenticatedFetch(url, options)` (Req 1.7, 1.8)
- `transcription.js` — `transcribeDeepgram(input)`, `transcribeKhaya(input)`, `fetchKhayaLanguages()`, `normalizeResponse(raw, provider)` (Req 1.1–1.6, 1.13)
- `history-repo.js` — preserved contract with key `deepgram_transcription_history`, max 50 (Req 1.9–1.12)
- `project-repo.js` — new, key `transcription_projects` (Req 10)
- `export/` — `exportTranscript(format, transcript)` returning Blob (Req 8.6, 9.6, 9.7)

### Data Models

Same `Transcript`, `TranscriptSegment`, `HistoryEntry`, `Project` shapes as the vanilla design. The history entry contract is preserved verbatim for backward compatibility with existing localStorage data.

## Component Library

React components styled with MD3 tokens. Each accepts standard props and forwards refs where useful.

| Component | Key props | MD3 mapping |
|-----------|-----------|-------------|
| `Button` | variant, icon, loading, disabled, onClick | Label Large, full radius |
| `Card` | variant (filled/outlined), header/children/actions | medium radius, tonal surface |
| `FormField` | label, error, help, children (control) | filled text field |
| `Tabs` | items, activeIndex, onChange | Primary tabs |
| `Table` | columns, rows, sortable, onSort | list/table styles |
| `Tooltip` | content, children | plain tooltip |
| `Toast` | severity, message, onDismiss | snackbar |
| `Progress` | value, indeterminate, label | linear/circular |
| `Skeleton` | variant (text/rect/circle), width, height | shimmer |
| `EmptyState` | icon, title, body, action | — |

## Animation Layer

**Framer Motion** (or Motion One) for React-native animation support:
- Page transitions via `<AnimatePresence>` and `motion.div` wrapping page outlets
- Upload zone interaction, progress, hover, skeleton shimmer, transcript generation indicator
- MD3 easing tokens mapped to Framer Motion transition configs
- `useReducedMotion()` hook to disable/minimize animations (Req 16.4)

## Performance

- `React.lazy()` + `<Suspense>` for per-page code splitting (Req 18.1, 18.2)
- `react-window` or custom VirtualList for transcripts > 500 segments (Req 18.3)
- Lazy import of heavy libs (waveform, DOCX, PDF) at point of use (Req 18.4)
- Image optimization and deferred loading for Unsplash assets

## Accessibility

- Semantic HTML elements (`<nav>`, `<main>`, `<header>`, `<footer>`, `<article>`)
- React Router's `<NavLink>` auto-applies `aria-current`
- Focus management on route changes (move focus to page heading)
- `aria-label`, `aria-describedby`, `aria-expanded` on interactive controls
- Visible focus indicators via `:focus-visible` styles
- Responsive layouts across mobile/tablet/desktop breakpoints
- WCAG 2.2 AA where practical (Req 17)
