FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y nano mailutils telnet default-mysql-client iputils-ping libmariadb-dev libmariadb-dev-compat pkg-config gcc libmagic1 && rm -rf /var/lib/apt/lists/*
# === INSTALL MINIMAL LIBREOFFICE (HEADLESS + WRITER ONLY) ===
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-common \
        fonts-dejavu-core \
        fonts-liberation \
        ca-certificates \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
           /usr/share/doc/* \
           /usr/share/man/* \
           /usr/share/locale/*
RUN pip install --no-cache-dir -r requirements.txt

# --- PATCH FROZEN-FLASK FOR FLASK 3.x COMPATIBILITY ---
RUN sed -i 's/url_encoding = self.app.url_map.charset/url_encoding = "utf-8"/g' /usr/local/lib/python3.12/site-packages/flask_frozen/__init__.py
# --- END PATCH ---
    
# Create non-root user and set permissions
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app


#COPY ./app /app

RUN python -m compileall /app
#

#RUN chown -R appuser:appuser /app/__pycache__
#RUN chmod -R u+rwx /app/__pycache__

USER appuser
