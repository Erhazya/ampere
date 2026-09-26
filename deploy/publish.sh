#!/usr/bin/env bash
# Publishes the image built for this commit to GHCR (ADR 020), from the CI's publish job.
# The sha-<commit> tag never changes: a re-run keeps the image already published for the commit.
# The main tag only moves while the commit is still the tip of main, so it never goes back.
# Usage: deploy/publish.sh IMAGE, with the variables GitHub Actions sets (GITHUB_TOKEN,
# GITHUB_ACTOR, GITHUB_SHA, GITHUB_REPOSITORY), IMAGE:sha-<commit> being already built.
set -euo pipefail

image=$1
commit_tag=$image:sha-$GITHUB_SHA

echo "$GITHUB_TOKEN" | docker login ghcr.io --username "$GITHUB_ACTOR" --password-stdin

# A missing tag answers "manifest unknown"; before its first publication, the package itself
# may answer "denied". The token can write, hence read, the package: "denied" never hides an
# existing tag, and without the right to write, the push fails. Any other error stops the job.
if answer=$(docker manifest inspect "$commit_tag" 2>&1); then
  echo "$commit_tag is already published: kept as it is."
elif grep --quiet --ignore-case --extended-regexp 'manifest unknown|name unknown|denied' <<< "$answer"; then
  docker push "$commit_tag"
else
  echo "Cannot tell whether $commit_tag exists: $answer" >&2
  exit 1
fi

tip=$(gh api "repos/$GITHUB_REPOSITORY/commits/main" --jq .sha)
if [[ $tip == "$GITHUB_SHA" ]]; then
  # The registry copies the commit's manifest under main as it is, without downloading the image:
  # --prefer-index=false keeps it from wrapping the manifest in an index with its own digest.
  docker buildx imagetools create --prefer-index=false --tag "$image:main" "$commit_tag"
else
  echo "main has moved on to $tip: the main tag is left to that commit's run."
fi
