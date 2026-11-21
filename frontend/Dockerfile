FROM node:22-slim AS builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY . .
RUN npm run build

# Unprivileged nginx image: runs as UID 101 and listens on 3000 (>1024).
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
ENV LAGENT_BACKEND_HOST=lagent-backend
# Used by nginx.mtls.conf; the plain nginx.conf ignores it.
ENV LAGENT_TLS_SERVER_NAME=agent
COPY nginx.conf /etc/nginx/templates/default.conf.template
USER 101
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
