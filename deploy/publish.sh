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

# An error other than "unknown" (a refused token, for example) stops the job, rather than
# being taken for a missing tag and overwriting one.
if answer=$(docker manifest inspect "$commit_tag" 2>&1); then
  echo "$commit_tag is already published: kept as it is."
elif grep --quiet --ignore-case --extended-regexp 'manifest unknown|name unknown' <<< "$answer"; then
  docker push "$commit_tag"
else
  echo "Cannot tell whether $commit_tag exists: $answer" >&2
  exit 1
fi

tip=$(gh api "repos/$GITHUB_REPOSITORY/commits/main" --jq .sha)
if [[ $tip == "$GITHUB_SHA" ]]; then
  # The registry points main at the image published for the commit, without downloading it.
  docker buildx imagetools create --tag "$image:main" "$commit_tag"
else
  echo "main has moved on to $tip: the main tag is left to that commit's run."
fi
