FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /cache/fastf1 /cache/on-demand
ENV FASTF1_CACHE=/cache/fastf1 ON_DEMAND_CACHE=/cache/on-demand ON_DEMAND_ENABLED=true
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
