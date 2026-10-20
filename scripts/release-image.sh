#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:?set IMAGE}"
VERSION="${VERSION:-$(date -u +%Y%m%d%H%M%S)}"
GIT_SHA="$(git rev-parse HEAD)"

docker build -f Dockerfile.app -t "${IMAGE}:${VERSION}" -t "${IMAGE}:${GIT_SHA}" .
docker push "${IMAGE}:${VERSION}"
docker push "${IMAGE}:${GIT_SHA}"
printf 'published %s:%s and %s:%s\n' "${IMAGE}" "${VERSION}" "${IMAGE}" "${GIT_SHA}"
