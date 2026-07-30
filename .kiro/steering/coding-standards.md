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
- Any function returning a Promise must be `await`ed at every call site — an un-awaited async call passed to `res.json()` silently serializes as an empty object with no thrown error

## Python Style (`services/postprocess/`)

- Python 3.12+, `from __future__ import annotations`, full type hints
- Config via `pydantic-settings`; request/response models via Pydantic
- Structured logging via `structlog` with event names like `llm.refiner.chunk_timeout`
- CPU-bound rule stages run under `asyncio.to_thread` so the event loop stays free for `/health`
- Errors surface through the same envelope shape as the gateway

## Frontend Conventions

- **React 19 + Vite 7** — functional components with hooks
- React Router v7 (`createBrowserRouter`, `<NavLink>`, `<Outlet>`)
- Framer Motion for animations (with reduced-motion fallbacks)
- Tailwind v4 + daisyUI own component/utility styling; the MD3 token layer (`--md-sys-color-*`, `--md-sys-typescale-*`, `--md-sys-shape-corner-*`) in `frontend/src/styles/` supplies design values and is bridged into Tailwind via `@theme` in `app.css`
- Font Awesome icons via CDN (`<i className="fa-solid fa-..." />`)
- LocalStorage for history and project persistence (via service modules)
- State managed through React Context + custom hooks
- Services layer is pure TS (no React dependency) — consumed by hooks
- No CSS-in-JS

## Backend Conventions

- Express 5.x with `cors` middleware
- Multer for file uploads (memory storage)
- JWT auth via `jsonwebtoken` — all protected routes use `requireSession` middleware
- Configuration via environment variables (dotenv, loaded with `override: true`)
- Structured error responses with type, code, and message fields
- TOML metadata file (`deepgram.toml`) for app info
- New endpoint groups go in `routes/` as a factory that takes its dependencies (`requireSession`, `upload`, deps) and returns an `express.Router`
- Post-processing datasets (`lib/location-correction/*-dataset.js`) are the single source of truth for Ghana entities in `js` mode — both the rule-based engine and the Bedrock prompt read from them, so add new entities once. The Python service reads its own copies from PostgreSQL, seeded from `services/postprocess/datasets/`.
- Bedrock/AWS SDK calls and Postprocessing Service calls must fail non-fatally — catch and log, degrade to a lesser result, never throw past the route handler

## Dependencies

- Only add dependencies that are strictly necessary
- Pin exact versions (no `^` or `~`) in `package.json`, `frontend/package.json`, and `pyproject.toml`
- Backend deps go in root `package.json`; frontend deps in `frontend/package.json`; Python deps in `services/postprocess/pyproject.toml`

## Git

- Conventional commits: `feat(scope):`, `fix(scope):`, `refactor(scope):`, `chore(scope):`
- Never include `Co-Authored-By` lines
- Scope is typically `node-transcription` or `deps`
