FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -c "from pathlib import Path; lines=Path('/tmp/requirements.txt').read_text(encoding='utf-8').splitlines(); kept=[ln for ln in lines if ln.strip() and not ln.lstrip().lower().startswith('pytest')]; Path('/tmp/runtime-requirements.txt').write_text('\n'.join(kept)+('\n' if kept else ''))" \
    && pip install --no-cache-dir -r /tmp/runtime-requirements.txt \
    && rm -f /tmp/requirements.txt /tmp/runtime-requirements.txt

COPY run.py /app/run.py
COPY src /app/src

ENTRYPOINT ["python", "run.py"]
