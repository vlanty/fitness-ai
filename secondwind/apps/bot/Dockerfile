# Build from the repository root:
#   docker build --platform linux/amd64 -f secondwind/apps/bot/Dockerfile -t ${BASE}/bot:latest .
FROM python:3.12-slim
WORKDIR /app
COPY requirements/bot.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# Build matplotlib's font cache at build time, not on the first chart after a cold start.
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot"
COPY secondwind/__init__.py secondwind/
COPY secondwind/core/ secondwind/core/
COPY secondwind/adapters/ secondwind/adapters/
COPY secondwind/apps/__init__.py secondwind/apps/
COPY secondwind/apps/bot/ secondwind/apps/bot/
CMD ["uvicorn", "secondwind.apps.bot.main:app", "--host", "0.0.0.0", "--port", "8080"]
