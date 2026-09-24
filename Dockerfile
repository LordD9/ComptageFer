FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
COPY comptagefer comptagefer
RUN pip install --no-cache-dir .

ENV COMPTAGEFER_DATA=/data
EXPOSE 8000
CMD ["uvicorn", "comptagefer.app:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
