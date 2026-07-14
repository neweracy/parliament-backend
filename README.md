# Node Transcription

A Node.js transcription app powered by Deepgram (Speech-to-Text) and Khaya AI (Ghanaian languages), with a Ghana-focused entity correction post-processing pipeline — rule-based datasets for locations, presidents/ministers, MPs, and political parties, plus an optional Amazon Bedrock (Claude) LLM pass for accuracy beyond the static datasets.

<!-- [**Live Demo \u2192**](#) -->

## Quick Start

Click the button below to fork the repo:

[![Fork on GitHub](https://img.shields.io/badge/Fork_on_GitHub-blue?logo=github)](https://github.com/deepgram-starters/node-transcription/fork)

## Local Development

<!--
### CLI

```bash
dg check
dg install
dg start
```
-->

### Makefile (Recommended)

```bash
make init
cp sample.env .env  # Add your DEEPGRAM_API_KEY (see below for optional keys)
make start
```

Open [http://localhost:8080](http://localhost:8080) in your browser.

### Node.js & pnpm

```bash
git clone --recurse-submodules https://github.com/deepgram-starters/node-transcription.git
cd node-transcription
pnpm install
cd frontend && pnpm install && cd ..
cp sample.env .env  # Add your DEEPGRAM_API_KEY (see below for optional keys)
```

### Optional Features

| Feature | Required env vars | Behavior if unset |
|---------|--------------------|--------------------|
| Khaya AI transcription (African languages) | `KHAYA_API_KEY` | Khaya endpoints return an error; Deepgram transcription is unaffected |
| Hybrid confidence-correction pipeline | `KHAYA_API_KEY` | `/api/transcription/hybrid` requires Khaya |
| Bedrock LLM post-processing | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Skipped silently — transcripts still get the rule-based Ghana entity correction |

See `sample.env` for the full list of variables and defaults.

Start both servers in separate terminals:

```bash
# Terminal 1 - Backend (port 8081)
node server.js

# Terminal 2 - Frontend (port 8080)
cd frontend && corepack pnpm run dev -- --port 8080 --no-open
```

Open [http://localhost:8080](http://localhost:8080) in your browser.

## License

MIT - See [LICENSE](./LICENSE)
