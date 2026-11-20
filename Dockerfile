FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home chest
COPY backend/ ./
COPY --from=frontend /build/dist /app/web/dist
RUN mkdir -p /app/data && chown -R chest:chest /app
USER chest
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
