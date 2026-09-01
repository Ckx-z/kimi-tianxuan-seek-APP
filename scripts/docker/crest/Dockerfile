# cof-crest image: CREST conformer search (conda-forge crest + xtb, linux/amd64)
# Purpose: Windows hosts cannot install CREST natively (no win-64 conda package,
#           no pip wheel); run it in a container instead. src/dft/conformers.py
#           mounts the host workdir and invokes `crest` inside this image.
# Build (China networks: pass mirror build-args, see setup_crest_docker.ps1):
#   docker build -t cof-crest:latest .
#   docker build \
#     --build-arg BASE_IMAGE=docker.m.daocloud.io/condaforge/miniforge3:latest \
#     --build-arg CONDA_CHANNEL=https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge \
#     -t cof-crest:latest .
# Verify: docker run --rm cof-crest:latest crest --version

# Base image: official Docker Hub by default; override via BASE_IMAGE for
# networks where registry-1.docker.io is unreachable (e.g. mainland China)
ARG BASE_IMAGE=condaforge/miniforge3:latest
FROM ${BASE_IMAGE}

# conda-forge `crest` pulls xtb / xtb-python as dependencies (linux x86_64)
# CONDA_CHANNEL override for mirror usage (e.g. TUNA conda-forge mirror)
ARG CONDA_CHANNEL=https://conda.anaconda.org/conda-forge
RUN conda install -y --override-channels -c ${CONDA_CHANNEL} crest xtb \
    && conda clean -afy

ENV PATH=/opt/conda/bin:$PATH
# xtb parameter files location (required when invoking crest/xtb directly
# inside the container, equivalent to conda activate)
ENV XTBPATH=/opt/conda/share/xtb
ENV OMP_NUM_THREADS=4
ENV OMP_STACKSIZE=1G

# Host workdir mount point: crest reads/writes input.xyz / crest_conformers.xyz here
WORKDIR /work

CMD ["crest", "--version"]
