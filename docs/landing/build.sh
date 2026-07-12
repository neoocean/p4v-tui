#!/usr/bin/env bash
# p4v-tui 랜딩 사이트 배포 번들 만들기.
#
# 이 디렉토리는 그 자체로 자족적(self-contained)이라 통째로 정적 호스팅에 올려도
# 됩니다(GitHub Pages: CNAME + .nojekyll 가 이미 있음). build.sh 는 배포용
# _dist/ 에 필요한 파일만 골라 복사하는 편의 스크립트입니다.
#
#   ./build.sh            # → ./_dist
#   ./build.sh /some/dir  # → /some/dir
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/_dist}"

rm -rf "$out"
mkdir -p "$out/image" "$out/guide"

# 페이지 · 스타일 · 스크립트
cp "$here"/index.html "$here"/guide.html "$here"/styles.css \
   "$here"/lightbox.js "$here"/guide-nav.js "$out/"
# 가이드 챕터
cp "$here"/guide/*.html "$out/guide/"
# 스크린샷(SVG)
cp "$here"/image/*.svg "$out/image/"
# GitHub Pages 마커
cp "$here"/CNAME "$here"/.nojekyll "$out/" 2>/dev/null || true

echo "built → $out"
echo "  pages : $(ls "$out"/*.html "$out"/guide/*.html | wc -l | tr -d ' ')"
echo "  images: $(ls "$out"/image/*.svg | wc -l | tr -d ' ')"
