---
inclusion: fileMatch
fileMatchPattern: "frontend/**"
---

# Frontend Development Guide

## Architecture

React + Vite single-page application with React Router for client-side routing. The frontend is a git submodule.

- `frontend/src/main.tsx` — React root render with RouterProvider
- `frontend/src/App.tsx` — App shell (ThemeProvider, NavBar, animated Outlet, Footer, focus-on-route-change)
- `frontend/src/router.tsx` — `createBrowserRouter` route table with React.lazy pages
- `frontend/vite.config.ts` — Vite + `@tailwindcss/vite` + `@vitejs/plugin-react`, `/api` proxy

## Stack

- **React 19** with TSX
- **React Router v7** (`createBrowserRouter`, `<NavLink>`, `<Outlet>`)
- **Framer Motion 11** for animations (`AnimatePresence`, reduced-motion fallbacks via `utils/motion.ts`)
- **Vite 7** for dev server, HMR, and production bundling
- **Tailwind v4 + daisyUI 5** for utilities and component styling, layered over MD3 design tokens
- **wavesurfer.js** for waveform rendering
- **Vitest** for tests (`pnpm test`)
- **TypeScript** throughout — no plain JS

## Vite Proxy

```typescript
proxy: {
  '/api': {
    target: process.env.VITE_BACKEND_URL || 'http://localhost:8081',
    changeOrigin: true
  }
}
```

Dev server is `port: 8080, strictPort: true`.

## Folder Structure

```
frontend/src/
  main.tsx              # React root
  App.tsx               # Shell: ThemeProvider, NavBar, Outlet, Footer
  router.tsx            # Route table with React.lazy pages
  contexts/             # ThemeContext
  hooks/                # useHistory, useProjects, useTheme, useTranscription
  services/             # session, transcription, history-repo, project-repo, metadata, locations, export/
  components/
    ui/                 # Button, Card, EmptyState, FormField, Progress, Skeleton, Table, Tabs, Toast, Tooltip
    layout/             # NavBar, Footer, PageTransition, Sidebar, ThemeToggle
    features/           # UploadZone, WaveformPlayer, TranscriptViewer, VirtualList, ResultsSidebar, LocationSelector
  pages/                # Landing, Transcribe, Projects, History, About, errors/{NotFound,ProcessingError,UnsupportedFormat}
  styles/               # app.css (Tailwind/daisyUI entry), tokens.css, theme-light.css, theme-dark.css, base.css
  utils/                # audio-formats, location-corrector, motion, search, validation
  assets/images/        # Optimized images + credits.ts
```

There is no AuthContext — session handling lives in `services/session.ts` (module-level cached token, `getSessionToken`, `authenticatedFetch`) and is imported directly.

## Component Patterns

- Functional components with hooks (no class components)
- Props destructured in function signature; typed via interfaces
- `forwardRef` when DOM access is needed by parent
- Custom hooks for shared stateful logic
- Services layer is pure TS (no React dependencies) — consumed by hooks

## Adding a New Page

1. Create `src/pages/MyPage.tsx` with a default export
2. Add a lazy import and a child route (wrapped in `<Suspense>`) in `src/router.tsx`
3. Add a NavLink in `src/components/layout/NavBar.tsx`

## Adding a New UI Component

1. Create in `src/components/ui/ComponentName.tsx`
2. Style with Tailwind/daisyUI utilities; reach for MD3 token variables from `styles/tokens.css` for values daisyUI doesn't own (surfaces, outlines, motion, shape)

## Styling

- `styles/app.css` is the entry point: imports Tailwind, declares the daisyUI `light`/`dark` themes, then imports `tokens.css`, `theme-light.css`, `theme-dark.css`, `base.css`
- MD3 token layer: `--md-sys-color-*`, `--md-sys-typescale-*`, `--md-sys-shape-corner-*`, `--md-sys-motion-*`; a small `--dg-*` alias block in `tokens.css` maps a subset onto Deepgram naming
- daisyUI owns `primary`/`secondary`/`accent`/`error`/`base-*` and their `-content` pairs. Do not redefine those in `@theme` — it breaks content contrast. Only MD3-specific extras (surface containers, outline, inverse-surface) are bridged into Tailwind.
- Light/dark themes via `[data-theme="light"]` / `[data-theme="dark"]` on `<html>`, set by ThemeProvider
- Font Awesome for icons: `<i className="fa-solid fa-icon-name" aria-hidden="true" />`
- Body text contrast ≥ 4.5:1 in both themes

## State Management

- **ThemeContext** — light/dark mode, persisted to `localStorage` key `theme`, seeded from `prefers-color-scheme`
- **services/session.ts** — cached JWT token + `authenticatedFetch`; clears the token and throws on 401
- **useHistory** — wraps `history-repo.ts` (localStorage CRUD)
- **useProjects** — wraps `project-repo.ts` (localStorage CRUD)
- **useTranscription** — request lifecycle (`idle → uploading → processing → complete | error`), normalizes the response, saves to history. Deepgram results are already corrected server-side, so the frontend location corrector is deliberately **not** re-applied to them; it only runs on Khaya results.

## Dependencies

- Pin exact versions (no `^` or `~`)
- Core: `react`, `react-dom`, `react-router-dom`, `framer-motion`, `wavesurfer.js`, `ghana-locations`
- Dev: `vite`, `@vitejs/plugin-react`, `tailwindcss`, `@tailwindcss/vite`, `daisyui`, `typescript`, `vitest`

## Running

```bash
cd frontend && corepack pnpm run dev -- --port 8080 --no-open
corepack pnpm test    # vitest run
corepack pnpm build   # vite build → dist/
```
