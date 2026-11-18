FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Build matplotlib's font cache at build time, not on the first chart after a cold start.
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot"
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
