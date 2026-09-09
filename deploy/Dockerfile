# Stage 1: Build custom Caddy with rate limiting module
FROM golang:1.25-alpine AS caddy-builder
WORKDIR /build
RUN go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
RUN xcaddy build --with github.com/mholt/caddy-ratelimit
# Binary output: /build/caddy

# Stage 2: Build frontend
FROM node:24-slim AS frontend-builder
RUN corepack enable
WORKDIR /build

# Cognito config baked into the SPA at build time (Vite reads VITE_* at build).
# Passed from the compose/build --build-arg set. Empty values fall back to the
# frontend's configuration-error page, so provide them for a real deployment.
ARG VITE_COGNITO_DOMAIN=""
ARG VITE_COGNITO_CLIENT_ID=""
ARG VITE_COGNITO_REDIRECT_URI=""
ARG VITE_COGNITO_LOGOUT_URI=""
ARG VITE_COGNITO_REGION="us-east-1"
ENV VITE_COGNITO_DOMAIN=$VITE_COGNITO_DOMAIN \
    VITE_COGNITO_CLIENT_ID=$VITE_COGNITO_CLIENT_ID \
    VITE_COGNITO_REDIRECT_URI=$VITE_COGNITO_REDIRECT_URI \
    VITE_COGNITO_LOGOUT_URI=$VITE_COGNITO_LOGOUT_URI \
    VITE_COGNITO_REGION=$VITE_COGNITO_REGION

COPY frontend/package.json frontend/pnpm-lock.yaml ./frontend/
RUN cd frontend && pnpm install --frozen-lockfile
COPY frontend/ ./frontend/
RUN cd frontend && pnpm build
# Inject Caddy template directive for subpath base path
RUN sed -i 's|<head>|<head>\n  <base href="{{ .Req.Header.Get "X-Base-Path" }}">|' ./frontend/dist/index.html

# Stage 3: Runtime
FROM node:24-slim
RUN corepack enable

# Copy custom Caddy binary
COPY --from=caddy-builder /build/caddy /usr/bin/caddy

WORKDIR /app

# Install backend production dependencies.
# pnpm blocks postinstall scripts by default, so ffmpeg-static never downloads
# its binary. Run its installer explicitly so audio transcoding works at runtime.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --prod && \
    node node_modules/ffmpeg-static/install.js

# Copy backend source and config
COPY *.js *.toml sample.env ./
COPY config.json* ./
# Application source directories required by server.js
COPY lib/ ./lib/
COPY routes/ ./routes/
COPY providers/ ./providers/
COPY middleware/ ./middleware/

# Copy built frontend
COPY --from=frontend-builder /build/frontend/dist/ ./frontend/dist/

# Copy deployment files
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/start.sh ./start.sh
RUN chmod +x ./start.sh

ENV PORT=8081
ENV HOST=0.0.0.0
EXPOSE 8080

ENV BACKEND_CMD="node server.js"
CMD ["./start.sh"]
