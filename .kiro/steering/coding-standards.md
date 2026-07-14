---
inclusion: always
---

# Coding Standards

## JavaScript Style

- Backend uses CommonJS (`require`/`module.exports`) — plain JavaScript, no TypeScript
- Frontend uses TypeScript with JSX (`.tsx` for components, `.ts` for services/utils/hooks)
- Use JSDoc comments for function documentation where helpful (required for exported backend functions)
- Prefer `const` over `let`; avoid `var`
- Use async/await over raw Promises
- Error responses follow a consistent structure: `{ error: { type, code, message } }`
- Any function returning a Promise (e.g. `async function`) must be `await`ed at every call site — an un-awaited async call passed to `res.json()` silently serializes as an empty object with no thrown error

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
- Post-processing datasets (`lib/location-correction/*-dataset.js`) are the single source of truth for Ghana entities — both the rule-based engine and the Bedrock prompt read from them, so add new entities once
- Bedrock/AWS SDK calls must fail non-fatally — catch and log, fall back to the rule-based result, never throw past the route handler

## Dependencies

- Only add dependencies that are strictly necessary
- Pin exact versions in package.json (no `^` or `~`)
- Backend deps go in root `package.json`
- Frontend deps go in `frontend/package.json`

## Git

- Conventional commits: `feat(scope):`, `fix(scope):`, `refactor(scope):`, `chore(scope):`
- Never include `Co-Authored-By` lines
- Scope is typically `node-transcription` or `deps`
