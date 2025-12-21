FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app

# Install g2 (and its non-ML deps). Torch is provided by the base image.
COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["g2"]
