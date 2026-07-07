---
inclusion: always
---

# Coding Standards

## JavaScript Style

- Backend uses CommonJS (`require`/`module.exports`)
- Frontend uses ES modules with JSX (`import`/`export`, `.jsx` extension for React components)
- No TypeScript — plain JavaScript with JSX
- Use JSDoc comments for function documentation where helpful
- Prefer `const` over `let`; avoid `var`
- Use async/await over raw Promises
- Error responses follow a consistent structure: `{ error: { type, code, message } }`

## Frontend Conventions

- **React + Vite** — functional components with hooks
- React Router v6 for client-side routing
- Framer Motion for animations (with reduced-motion fallbacks)
- Material Design 3 token layer (`--md-sys-color-*`, `--md-sys-typescale-*`, `--md-sys-shape-corner-*`) mapped onto Deepgram `dg-*` variables
- Font Awesome icons via CDN
- LocalStorage for history and project persistence (via service modules)
- State managed through React Context + custom hooks
- Services layer is pure JS (no React dependency) — consumed by hooks
- No CSS-in-JS; plain CSS with custom properties

## Backend Conventions

- Express 5.x with `cors` middleware
- Multer for file uploads (memory storage)
- JWT auth via `jsonwebtoken` — all protected routes use `requireSession` middleware
- Configuration via environment variables (dotenv)
- Structured error responses with type, code, and message fields
- TOML metadata file (`deepgram.toml`) for app info

## Dependencies

- Only add dependencies that are strictly necessary
- Pin exact versions in package.json (no `^` or `~`)
- Backend deps go in root `package.json`
- Frontend deps go in `frontend/package.json`

## Git

- Conventional commits: `feat(scope):`, `fix(scope):`, `refactor(scope):`, `chore(scope):`
- Never include `Co-Authored-By` lines
- Scope is typically `node-transcription` or `deps`
