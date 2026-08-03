FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# El bytecode se escribe FUERA del arbol montado. compose monta `.:/app`, asi que sin
# esto los .pyc del interprete del host y los del contenedor comparten carpeta y se
# pisan: se observo una primera ejecucion de la suite en rojo por bytecode que no
# correspondia al fuente, verde a partir de la segunda.
ENV PYTHONPYCACHEPREFIX=/tmp/pycache

# El scheduler del run diario se enciende AQUI, pegado al comando que arranca la web,
# de modo que `docker compose run --rm app pytest ...` y `... python -m app.cli ...`,
# que SUSTITUYEN el comando, no lo hereden encendido por defecto.
#
# OJO, esto solo cubre el valor POR DEFECTO: compose declara `env_file: .env`, asi que
# un SCHEDULER_ACTIVO=1 escrito en el .env se inyecta en CUALQUIER comando, pytest
# incluido. La suite se protege aparte, apagandolo en tests/conftest.py.
CMD ["sh", "-c", "export SCHEDULER_ACTIVO=${SCHEDULER_ACTIVO:-1}; exec uvicorn app.web.main:app --host 0.0.0.0 --port 8000"]
