# Stock_Picker web UI — see DEPLOY.md for the full tunnel setup.
FROM python:3.12-slim

WORKDIR /app

# Layer-cache the dependency install; code changes don't re-run pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8713

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8713/healthz', timeout=4)"

# Exactly one worker: the job runner is a process-global (webapp.JOB), so
# multiple workers would each get their own job state and log.
CMD ["uvicorn", "webapp:app", "--host", "0.0.0.0", "--port", "8713", "--workers", "1"]
