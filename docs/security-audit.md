# p4v-tui 보안 검토 (Security Audit)

> 검토일: 2026-06-07 · 대상: 현재 HEAD · 검토 방식: 소스 정적 분석
> (subprocess/역직렬화/SQL/렌더링/경로·파일 처리/외부 실행 표면 전수).

## 요약 (TL;DR)

p4v-tui 는 **기존 Perforce 서버의 읽기·쓰기 프론트엔드**다. 자체 네트워크
리스너·데몬·인증 저장소가 없고, 권한 경계는 전적으로 p4d + `p4` CLI 의
인증/프로텍션에 위임한다. 그만큼 공격 표면은 작지만, "프론트엔드라 무조건
안전"은 아니다 — 이 앱은 **신뢰할 수 없는 데이터(서버 응답·다른 사용자가
submit 한 파일/경로/CL 설명·열어 보는 파일 내용)를 로컬에서 역직렬화·렌더·
저장**한다. 그 경로들을 점검했다.

**결론: 심각(High) 취약점 0건.** 원격에서 트리거되는 RCE·injection 경로는
발견되지 않았다. 잔여 위험은 ① p4d 출력 `marshal` 역직렬화(이론적, 기존
P4Python 과 동일한 신뢰모델로 완화됨), ② **로컬 설정 파일 신뢰**(설계상
임의 로컬 실행 가능 — 사용자 본인 설정), ③ 방어적 강화 권장 몇 건뿐이다.

| # | 표면 | 심각도 | 상태 |
|---|---|---|---|
| F1 | `marshal.load` (p4d -G 출력) | Low (이론) | 완화됨 |
| F2 | 로컬 config = 임의 실행(macro / Open With) | Low (설계상) | 문서화 |
| F3 | 외부 브라우저로 URL 오픈(스킴 미검증) | Low | 강화 권장 |
| F4 | `p4` argv 인자 주입(`--` 미사용) | Low | 강화 권장 |
| F5 | 터미널 이스케이프/Rich markup 주입 | Low | 완화됨 |
| F6 | 로그/상태 파일의 정보 노출 | Info | 수용 |
| F7 | 악성 p4d 의 자원 고갈(DoS) | Low | 부분 완화 |

---

## 위협 모델 (Threat Model)

신뢰 경계와 "공격자"를 명시한다.

- **신뢰함**: 사용자가 명시적으로 설정한 p4d 서버(전송은 보통
  `ssl:host:1666`), 로컬 `p4` 바이너리, 사용자 본인의 설정 파일과 HOME.
  이는 P4Python/`p4` CLI 가 암묵적으로 신뢰하는 것과 동일한 경계다.
- **신뢰하지 않음(공격자 후보)**:
  1. **악성/하이재킹된 p4d 또는 MITM** — 조작된 tagged/marshal 응답,
     비정상적 경로·리비전·파일 내용 반환.
  2. **같은 depot 의 다른 사용자** — 악의적 **파일 이름 / depot 경로 /
     CL 설명 / 파일 내용**을 submit → 피해 사용자가 트리·상세·뷰어에서
     열람(저장형 주입 시도).
  3. **로컬 비권한 프로세스** — `~/.p4v-tui/` 상태·인덱스 파일 변조.
- **명시적 비대상**: 로컬에 코드 실행 권한이 이미 있는 공격자(설정 파일을
  바꿀 수 있으면 어차피 사용자 권한으로 실행 가능).

핵심 질문: **서버나 타 사용자가 제어하는 바이트가 로컬에서 위험한 sink
(shell·eval·역직렬화·SQL·터미널·파일 경로)에 도달하는가?**

---

## 점검 결과 — 깨끗한 항목 (확인 완료)

- **셸 주입 없음** — 코드 전체에 `shell=True` / `os.system` / `os.popen`
  **0건**. 모든 외부 실행은 `subprocess.Popen/run`에 **인자 리스트**로
  전달(`fs_actions.py`, `p4client.py`, 클립보드, `p4 set -q`). 경로에
  공백·메타문자가 있어도 단일 argv 원소라 셸 해석이 일어나지 않는다.
- **동적 코드 실행 없음** — `eval` / `exec` / `pickle` / `yaml.load` /
  `__import__`(사용자 입력 기반) **0건**. 설정은 표준 `tomllib`(코드 실행
  불가 파서)로 파싱.
- **SQL 인젝션 없음** — Fast Search 의 모든 쿼리가 **파라미터 바인딩(`?`)**
  사용. f-string 으로 끼워 넣는 것은 고정 컬럼명(`desc` vs `desc_lower`,
  2개 화이트리스트)뿐. 사용자/서버 값은 전부 `?` 로 전달
  (`search_index.py` query_files / query_files_filtered / changes 검색).
- **파일 뷰어는 서버측 읽기** — `Enter` 뷰어는 `p4 print -q <depot>` 결과를
  렌더한다(`app.py:_open_file_viewer`). 로컬 파일시스템을 직접 열지 않으므로
  `//depot/../../etc/...` 류 로컬 경로 traversal 이 성립하지 않는다.
- **상태/인덱스 경로 고정** — `state.json` / 검색 인덱스 / permalink /
  bookmark 는 모두 `Path.home()/.p4v-tui/...` 고정 경로. 서버·사용자 입력이
  쓰기 대상 경로에 들어가지 않는다(permalink·bookmark 는 depot 경로를
  파일명이 아니라 JSON **데이터**로만 저장).
- **인증 비취급** — login/SSO/MFA/password/ticket 을 의도적으로 다루지
  않는다(외부 `p4 login` 에 위임). 따라서 앱이 자격증명을 저장·로깅·전송할
  표면 자체가 없다.

---

## 잔여 위험 + 권장 조치

### F1 — `marshal.load` 로 p4d 출력 역직렬화 (Low, 이론적)

CLI 백엔드는 `p4 -G`(Python marshal) 출력을 `marshal.load()` 로 읽는다
(`p4client.py`). Python 문서는 "신뢰할 수 없는 소스의 marshal 을 풀지
말라"고 경고한다 — CPython marshal 은 eval 류 동작은 없지만 변형 입력에
대한 메모리 손상 CVE 이력이 있다.

- **완화(현재)**: ① 입력이 **사용자가 설정한 p4d 의 subprocess stdout**
  으로 한정(P4Python 이 자기 소켓을 믿는 것과 동일 경계), ② 전송은 통상
  SSL, ③ `EOFError`/`ValueError` 를 스트림 종료로 처리해 잘린 응답에
  견딤, ④ per-call 타임아웃으로 hang 차단. 이미 `docs/MEMORY.md` 의
  trust-boundary 노트에 기록됨.
- **권장(선택)**: 외부 출처 바이트를 `_read_marshal_stream` 에 흘려보내는
  코드가 생기면 그 시점에 `json`/`msgpack` 등 강화 파서로 전환. P4Python
  백엔드(`P4V_BACKEND=python`)는 marshal 경로를 아예 쓰지 않으므로,
  민감 환경에선 P4Python 백엔드를 권장.

### F2 — 로컬 설정 = 임의 로컬 실행 (Low, 설계상)

`p4v-tui.toml` 의 두 기능은 본질적으로 사용자 권한 임의 실행이다:
- **`[[macro]]`** — `kind="p4"` 단계가 임의 `p4` 인자를 실행
  (`self.p4.run(*step.args)`), 개별 키바인딩 등록 가능.
- **`[[editor]]` / Open With…** — 사용자가 지정한 커맨드를 실행
  (`fs_actions.open_with_external`).

이는 "내 에디터/매크로를 내가 설정"하는 의도된 기능이며, 입력은 **사용자
본인의 설정 파일**이다(원격 벡터 아님). 다만:
- **권장**: README/MANUAL 에 "**신뢰할 수 없는 `p4v-tui.toml` 을 받아
  실행하지 말 것**"을 명시(매크로/에디터 커맨드가 곧 코드 실행). 로컬
  설정 파일은 이미 `.gitignore` 대상이라 실수 커밋 위험은 낮다.

### F3 — 외부 브라우저 URL 오픈 시 스킴 미검증 (Low)

`webbrowser.open_new_tab(url)` 로 Swarm URL 을 연다(`app.py`). `url` 은
`{base}/changes/{N}` 또는 `{base}/files/{depot}` 로, `base` 는 사용자
설정(`[swarm] base_url`), 나머지는 CL 번호/depot 경로다. CL 번호는
`"default"` 만 거르고 **숫자 검증은 없다**. 현실 위험은 낮지만(base 가
사용자 설정), 방어적으로:
- **권장**: `webbrowser.open` 직전 URL 스킴을 `http`/`https` 화이트리스트로
  검증(`file:`/`javascript:` 등 차단), CL 식별자를 `str.isdigit()` 로 확인.

### F4 — `p4` argv 인자 주입 가능성 (Low)

`p4` 호출은 셸을 거치지 않지만, depot 경로/사용자 입력이 **명령 인자**로
들어간다(`("files","-e",glob)`, `("print","-q",path)` …). `--` 옵션
종결자를 쓰지 않으므로, 만약 `-`로 시작하는 값이 인자로 흘러가면 p4 가
이를 플래그로 오해할 여지가 있다.
- **현재 위험 낮음**: depot 경로는 `//` 앵커(`classify_path` 가 `//`
  요구), CL 은 숫자라 실제로 `-` 시작 값이 sink 에 닿기 어렵다.
- **권장**: 경로 인자 앞에 `--` 종결자를 두거나(`p4 print -q -- <path>`),
  Fast Search/Go-to-path 의 자유 입력을 p4 인자로 넘기기 전 `//` prefix
  검증. argument-injection 을 구조적으로 제거.

### F5 — 터미널 이스케이프 / Rich markup 주입 (Low, 완화됨)

서버·타 사용자 문자열(파일명·depot 경로·CL 설명·파일 내용)이 TUI 에
렌더된다. 점검 결과 주입 방어가 이미 적용돼 있다:
- CL 설명은 `rich.markup.escape()` 로 이스케이프 후 렌더
  (`app_details.py`) → `[bold]`·`[/INST]` 같은 markup 주입 무력화.
- 파일 뷰어 `RichLog(markup=False, highlight=False)` → 파일 내용을 Rich
  태그로 해석하지 않음(`file_viewer.py`).
- 트리 라벨은 `rich.text.Text(...)` 로 literal 렌더(`p4_tree.py`).
- raw ANSI/제어문자는 **Textual 의 셀 컴포지터**가 셀 단위로 합성하므로
  콘텐츠 속 `\x1b` 가 살아있는 이스케이프로 실행되지 않는다.
- **권장(선택·미적)**: `notify()` 토스트는 Rich markup 을 해석하므로,
  서버 유래 문자열(경로·에러 메시지)을 토스트에 넣을 때 `escape()` 하면
  깨진 markup 으로 인한 표시 글리치를 막을 수 있다(보안보다 표시 품질).

### F6 — 로그/상태 파일 정보 노출 (Info, 수용)

`~/.p4v-tui/`(state.json, last-error.log, 인덱스 sqlite)와 하단 Log 패널·
Command Monitor 는 실행한 `p4` 명령·depot 경로·서버 주소를 기록한다.
**자격증명은 포함되지 않는다**(인증은 외부 `p4`). depot 경로/서버명이
홈 디렉터리에 평문으로 남는 수준 — 다중 사용자 머신에선 HOME 퍼미션에
의존. 별도 조치 불요(수용), 단 버그 리포트에 로그 첨부 시 내부 경로
노출 주의.

### F7 — 악성 p4d 의 자원 고갈 (Low, 부분 완화)

조작된 p4d 가 거대한 응답을 반환하면 메모리/시간 압박이 가능하다.
- **완화(현재)**: 파일 뷰어 5MB cap + 청크 렌더, 검색 인덱스 디스크 cap,
  대량 작업 청킹, per-call 타임아웃, marshal 스트림의 EOF 처리.
- **잔여**: 단일 초대형 marshal 객체/단일 거대 행은 cap 이전에 메모리에
  올라올 수 있음(이론적). 신뢰하는 p4d 전제라 수용.

---

## 강화 권장 요약 (우선순위)

1. **F3/F4 (저비용, 권장)** — URL 스킴 화이트리스트 + CL `isdigit` 검증,
   p4 경로 인자에 `--` 종결자. 작은 패치로 두 잔여 표면을 구조 제거.
2. **F2 (문서)** — "신뢰할 수 없는 `p4v-tui.toml` 실행 금지" 경고를
   README/MANUAL 설정 절에 추가.
3. **F1 (선택)** — 민감 환경 권장 백엔드로 P4Python 명시(marshal 경로
   회피). 외부 출처 바이트가 생기면 강화 파서로 전환.
4. **F5 (미적)** — 토스트에 들어가는 서버 유래 문자열 `escape()`.

## 맺음

p4v-tui 의 보안 자세는 "얇은 프론트엔드 + 권한을 p4d/`p4`에 위임"이라는
설계 덕에 견고하다. 셸·eval·SQL injection 류의 고전적 원격 트리거 경로는
없고, 신뢰 불가 데이터의 렌더 경로는 escape/`markup=False`/Textual
컴포지터로 이미 방어된다. 남은 것은 이론적 위험(marshal)과 사용자 본인
설정에 대한 신뢰, 그리고 두어 건의 저비용 방어적 강화뿐이다. 위 1번
항목만 반영하면 잔여 코드-레벨 표면은 사실상 닫힌다.
