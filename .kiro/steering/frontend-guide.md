---
inclusion: fileMatch
fileMatchPattern: "frontend/**"
---

# Frontend Development Guide

## Architecture

React + Vite single-page application with React Router for client-side routing.

- `frontend/src/main.jsx` — React root render with RouterProvider
- `frontend/src/App.jsx` — App shell (NavBar, Outlet, Footer, context providers)
- `frontend/src/router.jsx` — Route definitions with React.lazy page components
- `frontend/vite.config.js` — Vite + @vitejs/plugin-react, /api proxy to backend

## Stack

- **React 18+** with JSX
- **React Router v6** (`createBrowserRouter`, `<NavLink>`, `<Outlet>`)
- **Framer Motion** for animations (with `useReducedMotion` fallbacks)
- **Vite** for dev server, HMR, and production bundling
- **No TypeScript** — plain JavaScript with JSX
- **No CSS-in-JS** — plain CSS with MD3 token custom properties

## Vite Proxy

The frontend proxies `/api` requests to the backend:
```javascript
proxy: {
  '/api': { target: 'http://localhost:8081', changeOrigin: true }
}
```

## Folder Structure

```
frontend/src/
  main.jsx              # React root
  App.jsx               # Shell: providers, NavBar, Outlet, Footer
  router.jsx            # Route table with React.lazy pages
  contexts/             # ThemeContext, AuthContext
  hooks/                # useAuth, useHistory, useProjects, useTranscription, useTheme
  services/             # Pure JS: session, transcription, history-repo, project-repo, metadata, export/
  components/
    ui/                 # Button, Card, FormField, Tabs, Table, Tooltip, Toast, Progress, Skeleton, EmptyState
    layout/             # NavBar, Footer, ThemeToggle, Sidebar
    features/           # UploadZone, Waveform, AudioPlayer, TranscriptViewer, VirtualList, ResultsSidebar
  pages/                # Landing, Transcribe, Projects, History, About, errors/
  styles/               # tokens.css, theme-light/dark.css, base.css, components.css
  utils/                # format.js, validation.js, audio-formats.js, search.js
  assets/images/        # Optimized images + credits.js
```

## Component Patterns

- Functional components with hooks (no class components)
- Props destructured in function signature
- `forwardRef` when DOM access is needed by parent
- Custom hooks for shared stateful logic
- Services layer is pure JS (no React dependencies) — consumed by hooks

## Adding a New Page

1. Create `src/pages/MyPage.jsx` with a default export
2. Add a lazy import and route in `src/router.jsx`
3. Add a NavLink in `src/components/layout/NavBar.jsx`

## Adding a New UI Component

1. Create in `src/components/ui/ComponentName.jsx`
2. Style with MD3 token CSS variables from `styles/tokens.css`
3. Use `dg-*` class mappings where they align (Button, Card, Input)

## Styling

- Material Design 3 token layer: `--md-sys-color-*`, `--md-sys-typescale-*`, `--md-sys-shape-corner-*`
- Mapped onto Deepgram `dg-*` variables where applicable
- Light/dark themes via `[data-theme="light"]` / `[data-theme="dark"]` on `<html>`
- Font Awesome for icons: `<i className="fa-solid fa-icon-name" />`
- Body text contrast ≥ 4.5:1 in both themes

## State Management

- **ThemeContext** — light/dark mode, persisted to localStorage
- **AuthContext** — session token, authenticatedFetch
- **useHistory hook** — wraps history-repo.js (localStorage CRUD)
- **useProjects hook** — wraps project-repo.js (localStorage CRUD)
- **useTranscription hook** — request lifecycle, normalize response, save to history

## Dependencies

- Pin exact versions (no `^` or `~`)
- Core: `react`, `react-dom`, `react-router-dom`, `framer-motion`
- Dev: `vite`, `@vitejs/plugin-react`
- Optional (lazy-loaded): waveform library, docx/pdf generators

## Running

```bash
cd frontend && corepack pnpm run dev -- --port 8080 --no-open
```
