# PatchPilot sandbox image.
#
# This image is defined entirely by PatchPilot. It never contains or runs code
# from a repository under analysis: the build context holds only this file and
# requirements.lock. Building needs network access to fetch the pinned wheels;
# running tests does not get any.

# Base image pinned by digest (python:3.12-slim, multi-architecture index).
FROM python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

COPY requirements.lock /opt/patchpilot/requirements.lock

# Hash-checked, wheels only: no package build script can run during the build.
RUN python -m pip install --require-hashes --only-binary=:all: --no-deps \
        -r /opt/patchpilot/requirements.lock

# Unprivileged user, and no setuid or setgid programs left in the image.
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /tmp \
        --shell /usr/sbin/nologin sandbox \
    && find / -xdev -type f -perm /6000 -exec chmod a-s {} +

USER 10001:10001
WORKDIR /workspace
