# 빠른 검색 (Fast Search) — 설계 시나리오

> 상태: **설계 안** (구현 전). 이 문서는 합의용 시나리오 — 실제 코드를
> 쓰기 전에 사용자가 동작을 검토할 수 있도록 모든 흐름을 글로 박아 둔다.

## 1. 동기

현재 `Ctrl+Shift+F` 의 **Find File** 은 매 호출마다 `p4 files -m 100 <pattern>`
를 서버로 던진다. 사용자가 한 글자 칠 때마다 :

- 서버 round-trip 200 ms ~ 2 s (네트워크 / 서버 부하에 따라).
- 결과가 다 도착할 때까지 화면이 빈다.
- 콘텐츠 검색은 아예 지원 안 함 — `p4 grep` 은 매 서버 호출이라 사실상
  돌릴 수 없는 비용.
- 검색 결과에서 파일을 살펴보려면 modal 을 닫고 트리로 가서 다시 navigate
  해야 한다.

요구사항 (사용자 발언) :

1. **속도 우선** — 타이핑마다 결과가 즉시 갱신 (Everything 앱처럼).
2. **기존 p4 검색을 포괄** — filename / 부분 path / 콘텐츠 / changelist
   description 등을 한 입력 박스에서.
3. **결과 내비게이션** — 키보드만으로 위/아래.
4. **plaintext 미리보기** — 결과 위 cursor 이동만으로 즉시 본문 확인.
5. **결과 하이라이트** — 검색어가 본문에 어디 어디 들어 있는지 시각적으로.

## 2. 기존 검색 대비 차이 한눈에

| 항목 | 기존 Find File | 새 Fast Search |
|---|---|---|
| 검색 단위 | filename (`p4 files`) | filename + content + CL description |
| 응답 시간 (filename) | 200 ms ~ 2 s / 키스트로크 | < 16 ms / 키스트로크 (로컬 인덱스) |
| 결과 갯수 cap | 100 | 200 (UI), 인덱스는 무제한 |
| 콘텐츠 검색 | ❌ | ✅ `p4 grep` 기반, opt-in, cancellable |
| 미리보기 | ❌ | ✅ 우측 패널 plaintext (스트림 렌더) |
| 매치 하이라이트 | ❌ | ✅ 결과 행 + 미리보기 양쪽 |
| 결과에서 트리 이동 | Enter 시 자동 이동 (이미 구현) | 동일 — 그대로 재사용 |
| 매치 간 점프 | ❌ | `n` / `N` (vim 식) |
| 오프라인 동작 | ❌ | ✅ filename 검색은 마지막 인덱스로 작동 |

## 3. 검색 모드

한 입력 박스에서 **모드는 자동 추론** 하되 prefix / suffix 로 명시 가능.

| 모드 | 기본 키 | prefix | 동작 |
|---|---|---|---|
| Filename + path | 기본 | 없음 | 모든 인덱스된 depot path 에 substring 매치 |
| Content (grep) | 명시 | `?` | `p4 grep -i -s` — 명시한 글로벌 영역 (또는 cursor 위치 트리) 내부 콘텐츠 |
| CL description | 명시 | `cl:` | `p4 changes -m 200 -l` 결과를 description 기준 substring |
| User filter | 명시 | `@user:` | filename 결과를 head CL 의 user 로 필터 |
| Type filter | 명시 | `type:` | filetype 이 매치되는 파일만 (`type:text` / `type:binary+l`) |
| 정규식 | 명시 | `/.../` | Python re — filename / content 양쪽에 적용 |

복수 토큰은 AND :
```
texture ?body cl:render @user:alice
```
→ "텍스처" path 가 있고, body 에 "body" 가 있고, head CL 의 description 에 "render" 가 있고,
head CL 의 user 가 alice 인 파일.

## 4. 인덱스 전략

### 4.1. 무엇을 인덱스하나

| 테이블 | 행 단위 | 컬럼 |
|---|---|---|
| `files` | depot path 1 개 | depot_path, lower, type, head_change, head_user, head_time, size |
| `changes` | CL 1 개 | change, user, time, desc, desc_lower |

`sqlite3` (CPython 표준 라이브러리) + **FTS5 virtual table** 로 본문 검색
없이도 LIKE / substring 이 즉시 떨어진다.

- `~/.p4v-tui/index/<server-hash>__<client>.sqlite` 한 파일에 모든 인덱스.
- WAL 모드 — 인덱서 worker 가 write 하는 동안 UI 가 read 가능.

### 4.2. 인덱스 빌드 흐름

- **최초 빌드** (또는 인덱스 파일 없음) :
  - `p4 files //...` 페이지네이션 (`-m 5000` 씩) 으로 enumerate.
  - 동시에 `p4 changes -m 5000 -l` 페이지로 description 인덱스.
  - JobStatusBar 에 진행률 + ETA. JobRunner 의 chunking 인프라 그대로
    사용 — 도중 종료해도 다음 실행에서 재개 가능.
  - 대형 depot (~1 M 파일) 기준 첫 빌드 5 ~ 10 분, SQLite 파일 ~ 100 MB
    추산.
- **증분 빌드** (인덱스 존재) :
  - 시작 시 `last_known_change` 보다 큰 CL 들을 `p4 changes -m 200` 으로
    먼저 가져옴 (대부분 < 1 s).
  - 영향 받은 파일들만 `p4 files @>last_known_change` 로 갱신.
  - 0 ~ 5 s 안에 동기화 완료.
- **수동 rebuild** : Preferences → Search → Rebuild Index 버튼.

### 4.3. 인덱스 신선도

- 인덱스 timestamp + 현재 `p4 counter change` 비교 → 헤더에 "Index: 3
  min ago, 12 CLs behind" 표시.
- 12 CLs 이상 behind 면 빠르게 (background) 증분 시작.

### 4.4. 인덱스 없이도 동작

- **인덱스 없음 → 자동 fallback**: 기존 `p4 files -m 100 <pattern>`
  방식 (현재 Find File 동일). 헤더에 "(no index — server search)" 안내.
- **인덱스 빌드 중** : 이미 인덱스된 부분에 대해서는 즉시 결과, 헤더에
  "Indexing... 42% done — results may be incomplete" 표시.

## 5. UI 구조

`Ctrl+F` 로 풀스크린 모달 `SearchModal` open. 두 영역 :

```
┌─ Search ──────────────────────────────────────────────────────────┐
│ Query: tex│ure_                                            ? (i)  │ ← Input
│ [path] [content] [desc]   12 ms · 84 files · ↑↓ jump · Enter open │ ← stats
├──────────────────────────────┬────────────────────────────────────┤
│ //depot/.../foo/tex.cpp    f │ //depot/.../foo/tex.cpp            │
│ //depot/.../texture.cpp    f │ #45 alice 2026-05-01               │
│ //depot/.../foo/texure.h   f │                                    │
│ //depot/baz/texgen.lua     f │ 12  void load_TEXURE(...) {         │
│ //depot/foo/dox.txt        c │ 13    auto* tex = load_tex();       │
│ ...                          │ 14    apply_TEXURE_filter(...);     │
│                              │ 15  }                               │
│ + 76 more                    │ — n/N jumps to next/prev match —    │
└──────────────────────────────┴────────────────────────────────────┘
```

- **상단 Input** : 검색어. 매 키스트로크마다 30 ms 디바운스 후 dispatch.
- **상태바** : 모드 chips (`path` / `content` / `desc` — 입력 prefix
  에 따라 자동 active), 응답 시간, 매치 수, 키 안내.
- **왼쪽 결과 리스트** : depot path + 우측 1 자 모드 마커
  (`f`=filename, `c`=content, `d`=description). 매치 substring 은 inline
  highlight (bold yellow).
- **오른쪽 미리보기** : cursor 가 가리키는 결과 행의 파일 내용. RichLog
  스트림 렌더 — 큰 파일도 첫 viewport 만 즉시 표시.

### 5.1. 미리보기 패널 동작

- 결과 cursor 가 행 위에 머무는 즉시 (~ 80 ms 디바운스) `p4 print -q`
  worker 가 그 파일을 fetch.
- 캐시 : `LRU(64)` — 직전에 본 64 개 파일은 즉시 표시 (네트워크 X).
- 파일 첫 viewport (~ 1 만 라인) 만 렌더, 나머지는 `↓` 로 더 내려갈 때
  on-demand.
- Binary 휴리스틱 (`NUL ≥ 1 %` in 첫 8 KiB) 매치 시 `[Binary — N bytes]`
  스텁만 표시, 검색은 path 매치로만 카운트.
- Content 모드 결과면 미리보기가 **매치 라인을 자동 스크롤** 해서
  viewport 중앙에 위치.

### 5.2. 하이라이트 규칙

- **검색어가 비어있지 않은** 동안 :
  - 결과 리스트 : 행 라벨 안에서 모든 substring 매치를 `bold yellow on
    default` 로 표시.
  - 미리보기 : 본문 안에서 모든 매치를 `black on yellow` 로 표시.
- 정규식 모드 (`/foo|bar/`) 이면 위 두 곳에 모두 그 regex 의 매치가
  하이라이트.
- 대소문자 :
  - 검색어가 전부 소문자 → 대소문자 무시 (smart-case, vim 식).
  - 한 글자라도 대문자 포함 → 대소문자 구분.

### 5.3. 네비게이션 / 키바인딩

| 키 | 동작 |
|---|---|
| 타이핑 | Input 에 입력, 결과 즉시 갱신 |
| `↑` / `↓` | 결과 행 이동 → 미리보기 자동 갱신 |
| `PgUp` / `PgDn` | 결과 행 페이지 점프 |
| `Tab` | 포커스 Input → 결과 리스트 → 미리보기 → Input 순환 |
| `n` / `N` | (미리보기 포커스) 다음 / 이전 매치 라인으로 점프 |
| `Enter` | 현재 결과를 트리에서 열기 — workspace 매핑되면 Workspace 탭, 아니면 Depot 탭, `navigate_to_path` 로 expand 후 cursor. modal close |
| `Ctrl+Enter` | 현재 결과를 새 FileViewerModal 로 풀스크린 view (현재 결과 modal 은 닫지 않음, 위에 stack) |
| `Esc` / `Ctrl+F` | 닫기 |
| `Ctrl+R` | 인덱스 즉시 rebuild (수동 트리거) |
| `Ctrl+Shift+L` | 결과 cap 토글 (200 → 2000 → 전체) — 큰 결과셋 명시적 확장 |
| `?` 위치에 hover | `?<query>` 모드 인 채로 content 검색 trigger 표시 |
| `Ctrl+G` | 강제 content 검색 (현재 query 그대로, content 모드 전환) |

## 6. 시나리오 — 구체적 사용 흐름

### 시나리오 A. "texture" 관련 파일 찾아 코드 보기

1. 사용자가 임의 화면에서 `Ctrl+F`.
2. 모달 열림, Input 자동 포커스, 빈 결과 (인덱스만 로드된 상태).
3. `t` 입력 → 30 ms 후 결과 84,201 행. UI 는 200 행 cap, 헤더 "200 of
   84201 — type more to narrow".
4. `tex` 입력 → 결과 837 행 → 200 of 837. 0 ms 표시 (다 메모리).
5. `texture` 입력 → 결과 42 행. 모두 표시. 첫 행 자동 cursor.
6. 미리보기 패널에 첫 행의 파일이 `p4 print -q` 후 표시 (~ 80 ms).
   매치 위치가 highlight + viewport 중앙으로 스크롤.
7. `↓ ↓ ↓` 로 cursor 이동 → 미리보기 즉시 갱신 (캐시 hit / 아니면
   ~ 100 ms).
8. `Enter` → 모달 닫힘, Workspace 트리가 해당 path 까지 expand 되어
   커서 안착.

### 시나리오 B. 콘텐츠 안에서 "load_texture" 호출처 찾기

1. `Ctrl+F` → `?load_texture`.
2. prefix `?` 가 감지되면 모드가 `content` 로 토글, 즉시 path 결과는
   숨김.
3. 콘텐츠 검색은 비싸므로 **300 ms 디바운스** 후 `p4 grep -i -s -e
   "load_texture" //depot/...` 워커 launch. 헤더에 spinner + "searching
   1284 files…".
4. `p4 grep` 결과가 스트리밍으로 도착 → 매 매치 1 개 들어올 때마다 결과
   리스트 append. 즉, 첫 매치는 grep 시작 ~ 200 ms 후 보임.
5. 새 키스트로크 들어오면 이전 grep 워커 **즉시 cancel** (P4Service.run
   exclusive group). 화면이 진행 중 grep 으로 점유되지 않음.
6. 매치 행 cursor → 미리보기 패널이 **매치 라인 중앙** 으로 자동 스크롤.
   `n` / `N` 로 그 파일 내부의 다른 매치들 사이 점프.
7. 옆 파일로 옮기려면 `↓`, 그러면 미리보기 그 파일로 갈아탐.

### 시나리오 C. 어제 CL description 에서 "fix crash" 검색

1. `Ctrl+F` → `cl:fix crash`.
2. prefix `cl:` → mode=description. path / content 결과 숨김.
3. 인덱스의 `changes` 테이블에서 즉시 매치 (~ 5 ms). 결과 17 CL.
4. 결과 행은 CL #, user, date, desc 첫 줄. cursor 이동 시 우측 패널이
   `p4 describe -s <CL>` 결과 (file list + 풀 desc) 표시.
5. `Enter` → 그 CL 을 Submitted 탭에 활성화 (해당 행 highlight) +
   모달 닫음.

### 시나리오 D. 오프라인 + 인덱스 있음

1. 노트북이 깨어났는데 P4 서버 unreachable. 앱이 reconnect 시도 중.
2. `Ctrl+F` 즉시 동작. 헤더에 "Offline — local index only".
3. filename / description 검색은 정상 동작 (모두 로컬).
4. content 검색은 `?` 입력 시 "p4 grep requires online connection.
   Reconnect or use filename mode." warning 후 비활성.
5. preview 패널 : 결과 cursor 이동 시 `p4 print` 시도 → 실패 → "Server
   unreachable — preview unavailable" stub. 캐시 hit 인 파일은 정상 표시.

### 시나리오 E. 인덱스 빌드 중

1. 첫 실행, Welcome modal 에서 "Build search index? (~ 5 min,
   ~ 100 MB disk)" 묻고 OK.
2. 백그라운드 인덱서 worker 가 JobRunner 에 enqueue. JobStatusBar 에
   "Indexing… 42 % · ETA 2 m 30 s" 표시.
3. 이 시점에 사용자가 `Ctrl+F` → 모달은 정상 열림. 헤더에 "Indexing
   42 % — results may be incomplete". 검색은 이미 색인된 부분에 대해
   즉시 답함.
4. 새 파일이 색인될 때마다 모달은 update — 사용자가 가만히 두면 결과
   리스트가 천천히 자라는 것이 보임 (단, throttle 해서 매 100 ms 에
   한 번만 redraw).
5. 인덱싱 완료 시 헤더 "Index: just now, fresh" 로 바뀜.

### 시나리오 F. 정규식 + 사용자 필터

1. `Ctrl+F` → `/Render.*Texture/i @user:alice`.
2. 모드 = filename + path, regex 켜짐, alice 의 head CL 인 파일만.
3. Python re 컴파일 (~ 0 ms), 인덱스 walk 하며 매치 (~ 10 ms for 100 k
   파일), alice 필터 적용.
4. 결과에 빨간색 "regex" chip + 매치 부분이 같은 색으로 표시.

## 7. 성능 예산

| 단계 | 목표 | 한계치 |
|---|---|---|
| 키스트로크 → 결과 list 첫 갱신 | < 16 ms | < 50 ms (60 fps 1 프레임) |
| 결과 cursor 이동 → 미리보기 첫 라인 표시 | < 80 ms (캐시) / < 250 ms (network) | < 1 s |
| 인덱스 증분 갱신 (1 만 CL behind) | < 5 s | < 30 s |
| content 검색 (`p4 grep`) 첫 결과 | < 500 ms (streaming) | < 3 s |
| Modal open → 사용 준비 | < 100 ms | < 300 ms |

미달 시 :
- 16 ms 못 맞추면 진단 모드 toast — "Index DB slow? Try Rebuild." 표시.
- 1 s 초과 시 preview 패널에 "(slow — still fetching)" indicator.

## 8. 하이라이트 세부

### 8.1. 결과 리스트 행 안에서

- 입력어 substring 들이 그 행의 텍스트에 매치되는 모든 위치를 `Rich`
  span 으로 wrap. style : `bold yellow`.
- 행 자체는 폭이 좁아 한 줄에 path + 마커. 매치 다 안 들어가면 leaf
  부분이 우선 보이도록 ellipsis (CJK-aware truncate, 이미 utils 에 있음).

### 8.2. 미리보기 안에서

- 본문 RichLog 의 각 라인에 매치 substring 들을 `black on yellow` 로
  highlight (Rich Text 가 segment 분리).
- 매치 라인은 line number 부분에도 작은 도트 (`•`) 마커. 화면 scroll
  position 에 따라 우측 mini-bar 에 매치들의 위치를 dot 으로 표시 (vim
  의 hlsearch + 우측 minimap 유사).

### 8.3. 매치 사이 점프 (`n` / `N`)

- preview 가 포커스를 가진 상태에서 `n` = 다음 매치 라인으로 scroll +
  cursor.
- `N` = 이전 매치 라인.
- 매치가 없으면 토스트 "no matches in preview".
- 매치 색은 vim 처럼 토글되지 않음 — 항상 visible.

## 9. SQL 인덱스 스키마 (잠정)

```sql
CREATE TABLE files (
    depot_path  TEXT PRIMARY KEY,
    lower       TEXT NOT NULL,            -- case-insensitive search
    leaf_lower  TEXT NOT NULL,            -- 보너스 정렬용 (leaf 매치 우선)
    type        TEXT,                     -- text, binary+l, etc.
    head_change INTEGER,
    head_user   TEXT,
    head_time   INTEGER,
    head_size   INTEGER
);

CREATE INDEX files_lower      ON files(lower);
CREATE INDEX files_leaf_lower ON files(leaf_lower);
CREATE INDEX files_head_user  ON files(head_user);
CREATE INDEX files_head_time  ON files(head_time DESC);

CREATE VIRTUAL TABLE files_fts USING fts5(
    depot_path,
    content='files',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE changes (
    change      INTEGER PRIMARY KEY,
    user        TEXT,
    time        INTEGER,
    desc        TEXT,
    desc_lower  TEXT NOT NULL
);

CREATE INDEX changes_user       ON changes(user);
CREATE INDEX changes_time       ON changes(time DESC);

CREATE VIRTUAL TABLE changes_fts USING fts5(
    desc,
    content='changes',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- meta: last_change, indexed_at, schema_version, server, client
```

쿼리 예 :
```sql
SELECT depot_path FROM files
 WHERE lower LIKE '%' || ? || '%'
 ORDER BY (CASE WHEN leaf_lower LIKE '%' || ? || '%' THEN 0 ELSE 1 END),
          head_time DESC
 LIMIT 200;
```

FTS5 보조 쿼리 :
```sql
SELECT depot_path FROM files_fts WHERE files_fts MATCH ? LIMIT 200;
```

## 10. 진입점 / 통합

- 글로벌 단축키 : `Ctrl+F` → 새 SearchModal.
- 기존 `Ctrl+Shift+F` Find File 모달은 그대로 유지 (단, 신규 SearchModal
  과 기능 겹치므로 deprecation 가능 — 다음 라운드에서 결정).
- Workspace / Depot 트리 컨텍스트 메뉴에 "Search In This Folder…" 신설
  → SearchModal 을 그 경로로 prefix-bound 시작 (`path:<cursor>/`).
- Preferences 에 `[search]` 섹션 :
  - `index.enabled = true|false`
  - `index.depots = ["//depot", "//projects/*"]`  — 인덱스 범위 좁히기
  - `preview.lru_size = 64`
  - `preview.viewport_lines = 10000`
  - `content_grep.timeout_seconds = 30`

## 11. 출하 범위 — v1 / v2

### v1 (이번 사이클)

- [x] SearchModal UI 골격 (두 패널 + Input + status bar).
- [x] SQLite 인덱서 (files 만, changes 는 v2).
- [x] filename + path substring 검색 (case-insensitive 기본, smart-case).
- [x] 결과 리스트 + cursor → 미리보기 자동 갱신.
- [x] 결과 행 inline match highlight.
- [x] 미리보기 본문 highlight + `n` / `N` 점프.
- [x] LRU(64) 미리보기 캐시.
- [x] 인덱스 빌드 / 증분 갱신 (JobRunner 통합, 도중 종료 후 재개).
- [x] 오프라인 fallback (인덱스만 사용).
- [x] Enter → 트리 navigate, Esc 닫기.

### v2 (다음 사이클)

- [x] `?<query>` content 검색 — `p4 grep` 스트리밍 (P4Python OutputHandler 기반, 매치 1건씩 ~150 ms 단위로 점진적 렌더).
- [x] `cl:` description 검색 + changes 테이블 (cold-cache 시 lazy seed).
- [x] `@user:` / `type:` / regex 필터 — `@user:VAL`, `type:VAL`, `/pat/flags`.
- [x] "Search In This Folder…" 컨텍스트 메뉴 통합 (Workspace / Depot 트리).
- [x] 결과 cap 토글 (200 / 2000 / unlimited) — `Ctrl+Shift+L`.
- [x] 매치 minimap — 미리보기 상태 줄에 40-셀 가로 dot bar 형식으로
  표시 (1차). 우측 vertical bar 는 layout 재편 필요해 후속.

### v3 (장기)

- [~] 자연어 검색 ("지난 주 alice 가 만진 텍스처 관련 파일")
  — **룰 기반 의도 파싱은 1차 완료** (CL 52567): `nl:` prefix +
  시간/사용자/CL 키워드를 인식해 기존 filtered 검색에 매핑.
  **임베딩 기반 re-ranker 는 후속** — 외부 모델 파일 의존성 /
  오프라인 동작 / 라이선스 / 토크나이저 결정 등을 별도 사이클에서
  다룸. 룰 기반 베이스라인이 이미 가용해 임베딩 미도입 상태로도
  사용 가능.
- [x] 오타 보정 ("textuer" → "texture" 제안) — Levenshtein ≤ 2 leaf
  매칭으로 1차 완료 (R2 와 함께). 다음 단계는 (a) 후보를 클릭/
  Enter 로 채택 가능하게 만들기, (b) 검색어 단위가 아닌 토큰 단위
  보정(`Render texuter` → `Render texture`).
- [x] 결과 리스트 안에서 inline diff 보기 — content 검색 (`?`) 결과
  행에 첫 매치 라인을 두 번째 줄로 함께 렌더 (truncate 100 cells,
  match 부분 bold yellow).
- [x] 다중 검색 탭 ("저장된 쿼리") — Ctrl+P / Ctrl+N 으로 최근 20개
  쿼리를 순환. 모달 인스턴스 간 App 의 _search_history 에 누적.
  실제 "탭 UI" 위젯은 후속 — 현재는 stack + 단축키 방식.

## 12. 엣지 케이스 / 의사 결정

- **공백이 들어있는 검색어** : `texture rendering` → AND. 두 토큰 모두
  하나의 path 에 들어 있어야 매치.
- **이스케이프** : `\?` 는 prefix `?` 가 아니라 리터럴 `?`. `\@` 도 동일.
- **인덱스 크기 폭주** : `[search].index.depots` 미설정이면 시작 시 모달
  로 "어떤 depot 만 인덱스할지" 물음. 사용자가 "전체" 선택 가능. 결정은
  Preferences 에 저장.
- **인덱스 파일 corrupt** : 시작 시 `PRAGMA integrity_check` 가 실패하면
  자동으로 rebuild 제안.
- **검색 모달 내부에서 paste** (Ctrl+V) : Input 의 일반 paste — 트리
  Ctrl+V 클립보드 흐름과 충돌 안 함 (포커스가 Input 위라 priority 가
  결정).
- **CJK 검색어** : SQLite FTS5 unicode61 tokenizer 가 한글 음절 분리.
  본문이 한글 description 인 경우 ngram 기반 추가 인덱스 v2 검토.
- **권한 없는 파일** : `p4 files` 결과는 사용자 권한에 따라 다름. 인덱스
  도 그에 맞춰지므로 다른 사용자가 빌드한 인덱스를 공유하지 않아야 함
  (인덱스 파일이 user/client 별로 분리되는 이유).

## 13. 위험 / 미해결

- **첫 빌드 시간** : 대형 depot 에서 5 ~ 10 분. 사용자가 그 시간 기다릴
  의향이 있는지 — Welcome modal 의 "Skip for now" 옵션 필요.
- **디스크 점유** : ~ 100 MB / 1 M 파일. SSD 여유 검사 + Preferences 토글.
- **인덱스 vs depot 일관성** : 서버에서 누가 obliterate 하면 인덱스에는
  남아 있음. `Ctrl+R` rebuild 가 명시적 회복 경로.
- **검색어 reactivity 의 비용** : 매 키스트로크마다 SQLite 가 200 행
  스캔 + Rich Text render 가 60 fps 를 위협할 수 있음. 측정 결과 16 ms
  내면 idle 디바운스 없이, 못 맞추면 30 ms 디바운스로 옵션화.

---

이 시나리오에 사용자 확인이 떨어지면 v1 구현으로 진입한다. 우선 합의가
필요한 결정 :

1. 진입점이 `Ctrl+F` 가 맞는지 (기존 Find File 의 `Ctrl+Shift+F` 옆에
   추가 vs 대체).
2. 인덱스 디스크 사용량 한도 (~ 100 MB 예상치 OK 인지).
3. 첫 빌드 후 자동 증분만으로 충분한지, 아니면 주기적 강제 rebuild 필요.
4. content 검색 trigger 가 prefix `?` 로 OK 한지 (다른 후보 : `/`, `>`
   등 — `/` 는 이미 트리 filter 점유 중).
