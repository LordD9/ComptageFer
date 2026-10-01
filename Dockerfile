FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
COPY comptagefer comptagefer
# Le pip livré par l'image de base peut être vulnérable ; mettre à jour l'outil
# avant de lui faire installer le projet, sans l'ajouter aux dépendances métier.
RUN pip install --upgrade --no-cache-dir "pip>=26.2" && pip install --no-cache-dir .

ENV COMPTAGEFER_DATA=/data
EXPOSE 8000
CMD ["uvicorn", "comptagefer.app:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
