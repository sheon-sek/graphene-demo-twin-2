FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json ./
RUN npm install
COPY apps/web ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY reference ./reference
COPY docs ./docs
COPY --from=web /web/dist ./apps/web/dist
RUN pip install --no-cache-dir .
EXPOSE 8080 4840
CMD ["graphene-twin","serve","--host","127.0.0.1","--port","8080"]
