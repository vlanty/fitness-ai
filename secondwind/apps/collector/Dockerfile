# Build from the repository root:
#   docker build --platform linux/amd64 -f secondwind/apps/collector/Dockerfile -t ${BASE}/collector:latest .
FROM python:3.12-slim
WORKDIR /app
COPY requirements/collector.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY secondwind/__init__.py secondwind/
COPY secondwind/core/ secondwind/core/
COPY secondwind/contracts/ secondwind/contracts/
COPY secondwind/adapters/ secondwind/adapters/
COPY secondwind/apps/__init__.py secondwind/apps/
COPY secondwind/apps/collector/ secondwind/apps/collector/
CMD ["python", "-m", "secondwind.apps.collector.main"]
