# Implementation Plan: Transcription App Redesign (React + Vite)

## Overview

This plan converts the approved design into incremental React + Vite coding tasks. Work proceeds in dependency order: project scaffolding → core infrastructure (contexts, hooks, services) → design system → component library → feature components → pages → wiring/accessibility/performance → verification.

Each task references the specific requirement clauses it satisfies. Tasks marked with `*` are optional.

## Tasks

- [x] 1. Scaffold React + Vite project
  - [x] 1.1 Initialize the React Vite project in `frontend/`
    - Remove existing vanilla JS files (main.js, index.html content); initialize with Vite + React template structure
    - Install and pin exact versions: `react`, `react-dom`, `react-router-dom`, `framer-motion`, `@vitejs/plugin-react`
    - Create `vite.config.js` with React plugin and `/api` proxy to `http://localhost:8081`
    - Create minimal `index.html` with React root mount point
    - Create `src/main.jsx` rendering `<RouterProvider>`
    - _Requirements: 2.1, 18.1_
  - [x] 1.2 Create folder structure
    - Create directories: `src/contexts/`, `src/hooks/`, `src/services/`, `src/services/export/`, `src/components/ui/`, `src/components/layout/`, `src/components/features/`, `src/pages/`, `src/pages/errors/`, `src/styles/`, `src/utils/`, `src/assets/images/`
    - _Requirements: 14.1_
  - [x] 1.3 Create `src/App.jsx` shell
    - App shell with NavBar, `<Outlet>` for React Router, Footer; wrap with ThemeProvider and AuthProvider
    - _Requirements: 2.2, 3.1_

- [x] 2. Implement routing with React Router
  - [x] 2.1 Create `src/router.jsx`
    - Define routes with `createBrowserRouter`: `/` (Landing), `/transcribe` (Transcribe), `/projects` (Projects), `/history` (History), `/about` (About), `*` (NotFound)
    - Use `React.lazy()` + `<Suspense>` for all page components
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 18.1, 18.2_
  - [x] 2.2 Handle `request_id` deep-linking
    - On `/transcribe?request_id=<id>`, load and display the matching history entry
    - On browser back/forward, React Router handles re-rendering automatically
    - _Requirements: 2.3, 2.6, 1.11_

- [x] 3. Implement contexts and state management
  - [x] 3.1 Create `src/contexts/ThemeContext.jsx`
    - Provide `theme`, `setTheme`, `toggleTheme`; persist to localStorage `theme` key; honor `prefers-color-scheme` on first load; apply `data-theme` attribute to `<html>`
    - _Requirements: 15.1, 15.2, 15.3_
  - [x] 3.2 Create `src/contexts/AuthContext.jsx`
    - Provide `authenticatedFetch`; internally manage token cache via `getSessionToken()`; on 401 clear token and signal refresh
    - _Requirements: 1.7, 1.8_

- [x] 4. Implement services (extracted from existing main.js)
  - [x] 4.1 Implement `src/services/session.js`
    - Port `getSessionToken()` and `authenticatedFetch()` preserving behavior: GET /api/session, Bearer header, 401 handling
    - _Requirements: 1.7, 1.8_
  - [x] 4.2 Implement `src/services/transcription.js`
    - `transcribeDeepgram(input)` → POST /api/transcription (URL or file, model incl. nova-3)
    - `transcribeKhaya(input)` → POST /api/khaya/transcription (file + language)
    - `fetchKhayaLanguages()` → GET /api/khaya/languages
    - `normalizeResponse(raw, provider)` → unified Transcript shape
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.13_
  - [x] 4.3 Implement `src/services/history-repo.js`
    - Preserved contract: key `deepgram_transcription_history`, max 50 entries; `getHistory`, `saveEntry`, `getEntryById`, `clearHistory`
    - _Requirements: 1.9, 1.10, 1.11, 11.5_
  - [x] 4.4 Implement `src/services/project-repo.js`
    - New: key `transcription_projects`; CRUD with rename/delete/duplicate; optional `historyId` link
    - _Requirements: 10.5, 10.6, 10.7_
  - [x] 4.5 Implement `src/services/metadata.js`
    - `fetchMetadata()` calling GET /api/metadata
    - _Requirements: 1.14_
  - [x] 4.6 Implement `src/utils/validation.js` and `src/utils/audio-formats.js`
    - Supported audio format set (MP3, WAV, M4A, FLAC, OGG, MP4); format-check predicates
    - _Requirements: 5.2_
  - [x] 4.7 Implement `src/utils/search.js`
    - Pure `filterBySearch(items, term, keyFn)` and `sortBy(items, field, direction)`
    - _Requirements: 10.2, 10.3, 10.4, 11.2, 11.3_

- [x] 5. Implement custom hooks
  - [x] 5.1 Create `src/hooks/useAuth.js`
    - Consume AuthContext; expose `authenticatedFetch`
    - _Requirements: 1.7_
  - [x] 5.2 Create `src/hooks/useHistory.js`
    - Wrap `history-repo.js` with React state; provide `entries`, `saveEntry`, `getById`, `clearAll`
    - _Requirements: 1.9, 1.10, 1.11, 1.12, 11.5_
  - [x] 5.3 Create `src/hooks/useProjects.js`
    - Wrap `project-repo.js` with React state; provide `projects`, `create`, `rename`, `delete`, `duplicate`, search/sort/filter
    - _Requirements: 10.2, 10.5, 10.6, 10.7_
  - [x] 5.4 Create `src/hooks/useTranscription.js`
    - Manage request lifecycle: idle → uploading → processing → complete | error; call transcription service; normalize response; save to history
    - _Requirements: 7.1, 7.2, 7.5, 1.9_
  - [x] 5.5 Create `src/hooks/useTheme.js`
    - Consume ThemeContext for components
    - _Requirements: 15.1, 15.2_

- [ ] 6. Implement MD3 design tokens and styles
  - [x] 6.1 Create `src/styles/tokens.css`
    - Define `--md-sys-color-*`, `--md-sys-typescale-*`, `--md-sys-shape-corner-*`, motion tokens mapped onto `dg-*` variables
    - _Requirements: 14.2, 14.3, 14.4, 14.5_
  - [x] 6.2 Create `src/styles/theme-light.css` and `theme-dark.css`
    - Light/dark color-role values; one primary accent; neutral surfaces; ≥4.5:1 contrast for text
    - _Requirements: 15.1, 15.4, 15.5_
  - [x] 6.3 Create `src/styles/base.css` and `components.css`
    - Base layout with ~1040px max-width; elevation via tonal surface; responsive breakpoints; visible focus indicators
    - _Requirements: 14.5, 14.6, 17.2, 17.5_

- [x] 7. Copy and register Unsplash image assets
  - Copy the four Unsplash images from `D:\code\transcript-end\frrontend-images\` into `frontend/public/images/`
  - Create `src/assets/images/credits.js` with photographer attribution data
  - _Requirements: 4.3, 12.2, 18.4_

- [x] 8. Implement reusable UI component library
  - [x] 8.1 Implement `Button.jsx`, `Card.jsx`, `FormField.jsx`
    - Button (variant: filled/tonal/text/outlined, icon, loading, disabled); Card (variant, header/children/actions); FormField (label/error/help with aria-describedby)
    - _Requirements: 14.1, 14.2, 17.3_
  - [x] 8.2 Implement `Tabs.jsx`, `Table.jsx`, `Tooltip.jsx`
    - Tabs with role=tablist and arrow-key nav; Table with sortable headers; Tooltip with hover/focus
    - _Requirements: 14.1, 17.1, 17.3_
  - [x] 8.3 Implement `Toast.jsx`, `Progress.jsx`, `Skeleton.jsx`, `EmptyState.jsx`
    - Toast (role=status/alert, auto-dismiss); Progress (determinate/indeterminate); Skeleton (reduced-motion shimmer); EmptyState
    - _Requirements: 14.1, 16.2, 16.4_

- [x] 9. Implement layout components
  - [x] 9.1 Implement `NavBar.jsx`
    - React Router `<NavLink>` for active state; hamburger below mobile breakpoint with aria-expanded; logo + links (Home/Transcribe/Projects/History/About); ThemeToggle
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  - [x] 9.2 Implement `Footer.jsx`
    - About link, GitHub repo, Contact, License, version, Unsplash attribution
    - _Requirements: 4.7, 12.2_
  - [x] 9.3 Implement `ThemeToggle.jsx`
    - Bind to ThemeContext; keyboard operable with ARIA label
    - _Requirements: 15.1, 15.2, 17.1, 17.3_
  - [x] 9.4 Implement `Sidebar.jsx`
    - Reusable sidebar layout for the Transcribe page (left: shortcuts, right: results)
    - _Requirements: 5.7, 9.1_

- [x] 10. Implement feature components
  - [x] 10.1 Implement `UploadZone.jsx`
    - Drag-and-drop + file picker; validate against supported formats; reject unsupported with error; show file name/size/preview; emit progress
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6, 16.2_
  - [x] 10.2 Implement `Waveform.jsx`
    - Waveform visualization; heavy lib lazily imported; seek surface for audio player
    - _Requirements: 5.5, 18.4_
  - [x] 10.3 Implement `AudioPlayer.jsx`
    - Wrap `<audio>` with play/pause/seek controls; expose `seekTo(seconds)` via ref/callback
    - _Requirements: 8.7, 8.8_
  - [x] 10.4 Implement `VirtualList.jsx`
    - Windowed renderer (react-window or custom); only visible items + overscan rendered
    - _Requirements: 8.9, 18.3_
  - [x] 10.5 Implement `TranscriptViewer.jsx`
    - Render segments (speaker labels, timestamps, confidence); inline edit; search; find-and-replace; copy; download via export service; clickable timestamps → seekTo; use VirtualList when segments > 500
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9_
  - [x] 10.6 Implement `ResultsSidebar.jsx`
    - AI summary, speakers, keywords, language, metadata when present; export buttons (TXT/DOCX/PDF/SRT/VTT)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

- [x] 11. Implement export formatters
  - [x] 11.1 Implement `src/services/export/txt.js`, `srt.js`, `vtt.js`, `index.js`
    - Pure string formatters; `exportTranscript(format, transcript)` returning Blob; trigger download
    - _Requirements: 8.6, 9.6, 9.7_
  - [x] 11.2 Implement DOCX/PDF formatters (lazy-loaded)
    - Library-backed; imported only when user selects these formats
    - _Requirements: 9.6, 9.7, 18.4_

- [x] 12. Implement animation layer
  - Configure Framer Motion (or Motion One) for page transitions via `<AnimatePresence>` wrapping the outlet
  - Add motion to: upload interaction, processing progress, hover effects, skeleton shimmer, transcript generation indicator
  - Map MD3 easing/duration tokens to transition configs
  - Use `useReducedMotion()` to disable non-essential animations
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 3.6_

- [x] 13. Implement the Landing page
  - Implement `src/pages/Landing.jsx`: hero with "Fast, Accurate AI Audio Transcription" headline, subheading, "Start Transcribing" (Link to /transcribe) + "Learn More" CTAs; transcription flow visual using Unsplash images; feature cards; three-step "How It Works"; sample transcript output; footer
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

- [x] 14. Implement the Transcribe page
  - [x] 14.1 Layout and upload
    - Compose UploadZone, waveform, audio preview, left sidebar (New Transcription, Projects, History links)
    - _Requirements: 5.1, 5.4, 5.5, 5.6, 5.7, 5.8_
  - [x] 14.2 Transcription controls
    - Provider selector (Deepgram/Khaya); language selector; toggles (speakers, timestamps, translation, summary, noise reduction); custom vocabulary; mark unavailable controls per provider
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 1.2, 1.5_
  - [x] 14.3 Processing status and results
    - Use `useTranscription` hook; show progress/stage/estimated time; processing logs toggle; on failure show error; on success render TranscriptViewer + ResultsSidebar; save to history; handle `?request_id=` deep-link
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 1.9, 1.11, 1.12_

- [x] 15. Implement the Projects page
  - Implement `src/pages/Projects.jsx`: project cards (file name, date, duration, status, language, speakers); search/sort/filter; rename/delete/duplicate; recent projects section; empty state
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9_

- [x] 16. Implement the History page
  - Implement `src/pages/History.jsx`: job list (file name, date, status, duration, language, exported formats); search/filter/sort; select job loads result; clear history; empty state
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

- [x] 17. Implement the About page
  - Implement `src/pages/About.jsx`: overview (demo/testing interface), features summary, tech stack, future improvements; Unsplash imagery with attribution
  - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [x] 18. Implement error pages
  - Implement `NotFound.jsx`, `ProcessingError.jsx`, `UnsupportedFormat.jsx`: clear explanations with action buttons back to the app
  - _Requirements: 13.1, 13.2, 13.3, 2.4, 5.3, 7.4_

- [x] 19. Accessibility hardening
  - Focus management on route change (move focus to page heading); visible `:focus-visible` indicators; ARIA labels for icon-only controls; semantic HTML; responsive layouts across breakpoints
  - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6_

- [x] 20. Performance verification
  - Verify each page loads as separate chunk via React.lazy; lazy-import heavy libs; defer non-critical images; VirtualList active for long transcripts
  - _Requirements: 18.1, 18.2, 18.3, 18.4_

- [x] 21. Final build and verification
  - [x] 21.1 Run `vite build` and verify production output
    - Confirm code-split chunks, no build errors, /api proxy works with backend
    - _Requirements: 18.2_
  - [x] 21.2 Verify provider flows preserved
    - Test Deepgram transcription (URL + file), Khaya transcription (file + language), session auth, history save/load/deep-link, metadata display
    - _Requirements: 1.1, 1.4, 1.7, 1.9, 1.11, 1.14_

## Notes

- Tasks marked `*` are optional (advanced waveform, DOCX/PDF export).
- The backend is unchanged. Only the `frontend/` directory is rebuilt.
- Existing localStorage data (`deepgram_transcription_history`) is backward-compatible — the new app reads old entries.
- The git submodule for `frontend/` will be replaced with a fresh React project.
