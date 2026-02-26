# SPDX-FileCopyrightText: 2026 Carnegie Mellon University - Satyalab
#
# SPDX-License-Identifier: GPL-2.0-only

FROM python:3.10-slim

LABEL org.opencontainers.image.vendor="CMU Satyalab" \
      org.opencontainers.image.authors=satya-group@lists.andrew.cmu.edu

ARG DEBIAN_FRONTEND=noninteractive

# Install build and runtime dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip

RUN pip3 install --upgrade pip

COPY . .
RUN python3 -m pip install -e .

# Expose ports
EXPOSE 5555 9099 8000

ENTRYPOINT ["python3", "main.py"]
