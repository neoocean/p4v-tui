# p4v-tui 랜딩 사이트

`p4v-tui` 소개 + 전체 기능 가이드 정적 사이트. 프레임워크·빌드 의존성 없는
순수 HTML/CSS/바닐라 JS 이며 **디렉토리째로** 정적 호스팅에 올리면 됩니다
(라이브: https://p4v-tui.woojinkim.org, GitHub Pages).

## 구성

```
index.html         랜딩(히어로 · 회복력 · 기능 교차 · 갤러리 · 설치 · 다운로드)
guide.html         가이드 개요(챕터 카드 그리드)
guide/<topic>.html 챕터 10개 — 본문만 두면 guide-nav.js 가 내비/목차/페이저/푸터 주입
styles.css         공용 스타일(다크 터미널 테마)
guide-nav.js       가이드 공용 크롬(빌드 없는 공유 파셜). 챕터 추가 = CH 배열 한 줄
lightbox.js        스크린샷 클릭 확대
image/*.svg        스크린샷(../image 의 자족 복사본, NN-name.svg)
CNAME .nojekyll    GitHub Pages 커스텀 도메인 + Jekyll 비활성
build.sh           _dist/ 배포 번들 생성(선택)
```

모든 HTML 은 `image/…` 상대 경로만 씁니다(상위 `../image/` 참조 없음) — 폴더가
자족적입니다.

## 스크린샷 재생성 · 동기화

스크린샷은 **실제 앱**을 Textual 헤드리스로 운전해 SVG 로 굽고, 폰트 비의존
벡터로 후처리한 것입니다(합성 데모 백엔드 — 실제 서버/계정 없음).

```bash
# 저장소 루트에서
python3 scripts/gen_screenshots.py            # 전체 → docs/image/*.svg
python3 scripts/gen_screenshots.py 13-submit  # 이름 매칭 장면만

# docs/image → 이 폴더의 자족 복사본으로 동기화
for f in image/*.svg; do cp "../image/$(basename "$f")" "$f"; done
```

## 배포

```bash
./build.sh          # → ./_dist (index/guide/styles/js/image/CNAME/.nojekyll)
```

`_dist/`(또는 이 폴더 전체)를 GitHub Pages / 정적 호스트에 올립니다.
