# -----------------------------------------------------------------------------
# Jenkins Performance Analyzer -- Frontend
# Build context is the PROJECT ROOT (set in docker-compose.yml)
# so all paths are relative to the repo root, not ./frontend.
# Stage 1: build React/Vite app with Node
# Stage 2: serve static files via nginx with API reverse-proxy
# -----------------------------------------------------------------------------

# -- Stage 1: Node builder ------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first for better layer caching.
# package.json lives at frontend/ relative to the project-root build context.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline 2>/dev/null || npm install

# Copy frontend source files
COPY frontend/ .

ARG VITE_API_URL=""
ENV VITE_API_URL=${VITE_API_URL}

RUN npm run build


# -- Stage 2: nginx static server -----------------------------------------------
FROM nginx:1.27-alpine AS production

LABEL org.opencontainers.image.title="Jenkins Analyzer Frontend"

RUN rm /etc/nginx/conf.d/default.conf

# nginx.conf is at nginx/ relative to the project-root build context
COPY nginx/nginx.conf /etc/nginx/conf.d/app.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
