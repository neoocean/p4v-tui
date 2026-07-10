# p4 CLI fallback — 설계 시나리오

> 상태: **구현 완료** (Phase A/B/C 모두 적용). 본 문서는 합의용
> 시나리오로 출발했으며, 코드 반영 후에도 폐기하지 않고 설계 의도 +
> 구현 결정 기록으로 남겨둔다. 동작 변경이 있을 때는 본문도 함께
> 갱신한다. 자세한 caller migration 메모는 `DESIGN.md` 의 "Backend
> split" 히스토리 노트, parity 보장은 `tests/test_p4client_live.py`
> 참조.

## 1. 동기

현재 p4v-tui 는 **P4Python** (`import P4`) 에 hard-dependency 가 있다.
새 머신에서 설치할 때 :

- 보통은 `pip install p4python` 한 줄로 wheel 이 떨어진다.
- 그러나 wheel 미제공 환경 (오래된 Linux, 일부 BSD, 비공식 Python
  버전) 에서는 P4API + 컴파일러 (`Xcode CLT` / `build-essential`)
  설치가 필요해진다.
- 더 좁은 환경 (이미 `p4` 명령은 깔려 있지만 Python 빌드 환경이
  없는 라즈베리파이, 컨테이너 안 sidecar, SSH-only 서버 등) 에서는
  P4Python 셋업이 사실상 불가능해 p4v-tui 자체를 못 쓰는 경우가
  생긴다.

목표 : **`p4` CLI 만 있으면 p4v-tui 가 P4Python 없이도 그대로
동작**. 의존성 가벼움 + 같은 워크플로 그대로 사용 가능.

> Out of scope : `p4` 명령 자체가 없는 환경. 그 경우 p4v-tui 는
> "p4 도구를 PATH 에 두라" 안내 후 종료한다. p4 CLI 는 Perforce 가
> 직접 배포하는 단일 바이너리라 어떤 환경에도 설치하기 쉽다.

## 2. 기존 동작 vs CLI fallback 한눈에

| 항목 | 기존 (P4Python) | CLI fallback |
|---|---|---|
| import 의존성 | `P4` 모듈 (C 확장) | 없음. `subprocess` 만 |
| 호출당 비용 | connection 풀 재사용(기본 4), in-process | 매 호출 fork+exec+TCP+auth |
| 호출당 지연 (로컬 서버) | ~2-10 ms | ~50-100 ms (3-10×) |
| 호출당 지연 (원격, RTT 80 ms) | ~80-150 ms | ~200-500 ms |
| Threading | P4 인스턴스 풀(스레드당 1개) | 호출별 독립 subprocess |
| 인증 | P4TICKETS / SSO 사전 완료 | 동일 |
| 의존 binary | `p4` (실행 시) + Python 모듈 | `p4` 만 |
| streaming output | `OutputHandler` 콜백 | `Popen` stdout readline |

## 3. 사용자 관점 흐름

### 시나리오 A. wheel 정상 설치 (현 상태)

1. 사용자가 `python3 -m venv .venv && .venv/bin/pip install -r
   requirements.txt` 실행.
2. p4python wheel 이 잘 깔려서 `import P4` 성공.
3. p4v-tui 가 P4Python backend 로 기동. 변화 없음.

### 시나리오 B. wheel 부재 + 빌드 환경 부재

1. 사용자가 `pip install p4python` 실행 → compile 실패. 또는 시간
   부족으로 그 시도조차 안 함.
2. `python p4v.py` 실행 시 친절 메시지에서 안내 :
   `❌ P4 모듈 없음 — pip install 또는 PEP 668 방법…`
3. **신규** : 그 메시지 하단에 추가 안내 :
   ```
   ℹ  P4Python 설치가 어렵다면 CLI 모드로도 실행할 수 있습니다.
      `p4` 명령만 PATH 에 있다면 자동으로 CLI 백엔드가 활성화됩니다.
      성능은 약간 떨어지지만 (호출당 ~50ms 추가) 같은 기능을 사용
      할 수 있습니다. `P4V_BACKEND=cli python p4v.py` 로 강제 가능.
   ```
4. 사용자가 `P4V_BACKEND=cli python p4v.py` 또는 그냥 다시
   `python p4v.py` 실행 (P4 import 실패 시 자동 fallback).
5. p4v-tui 가 CLI backend 로 기동. 동작은 P4Python 모드와 동일.
   ConnectionBar / Pending CL / 트리 / Fast Search 모두 정상.

### 시나리오 C. 명시적 CLI 강제 (디버깅 / 비교)

1. `P4V_BACKEND=cli` 또는 `--backend cli` (CLI 인자) 로 강제.
2. P4Python 이 설치돼 있어도 CLI 백엔드 사용.
3. 시작 시 Log 패널에 "Backend: p4 CLI (forced)" 표시.

### 시나리오 D. `p4` 명령도 없음

1. P4Python 도 없고 `p4` 도 없음.
2. `python p4v.py` 친절 메시지 :
   ```
   ❌  Perforce 클라이언트가 PATH 에 없습니다.

   다음 중 하나를 설치하세요 :

     • P4Python (Python 바인딩) — pip install p4python
     • p4 CLI                  — https://www.perforce.com/downloads
   ```
3. 종료 코드 1.

## 4. Backend 아키텍처

`P4Service` 가 façade 로 남고, 내부 `_backend` 가 두 구현 중 하나 :

```
P4Service (외부 API 유지)
  ├─ port / user / client / charset / connected      ─┐
  ├─ run(*args) -> list[dict|str]                     │  공개 메서드는
  ├─ depots() / dirs() / files() / fstat()            │  형식과 시그니처
  ├─ describe() / filelog() / opened_in_change()      │  유지 — 호출자
  ├─ where()                                          │  영향 없음.
  ├─ pending_changes() / submitted_changes()          │
  ├─ fetch_client_view() / get_changelist_form()      │
  ├─ create_changelist() / update_changelist_desc()   │
  ├─ grep() / grep_stream()                           │
  ├─ login_status() / info()                          ─┘
  │
  └─ _backend
       ├─ _PythonBackend    (기존 코드)
       └─ _CLIBackend       (신규)
```

`P4Service.run` 등은 내부에서 `self._backend.run(*args)` 위임.

### Backend 선택 로직

```
def _make_backend():
    forced = os.environ.get("P4V_BACKEND") or get_pref()  # "python" / "cli"
    if forced == "cli":
        if not shutil.which("p4"):
            raise SetupError("`p4` 명령을 PATH 에서 찾지 못했습니다.")
        return _CLIBackend()
    if forced == "python":
        import P4  # raises if missing — 친절 메시지로
        return _PythonBackend()
    # auto
    try:
        import P4
        return _PythonBackend()
    except ImportError:
        if shutil.which("p4"):
            return _CLIBackend()
        raise SetupError("P4Python / p4 CLI 모두 없습니다…")
```

선택 결과는 `_on_connected` 직후 LogPanel 에 `log_info` 로 한 줄
기록 — "Backend: P4Python 2025.2" 또는 "Backend: p4 CLI 2025.2/Mac".

## 5. CLI 호출 방식

### 5.1. 일반 명령 — `-G` (Python marshal) 채택

`p4 -G <args>` 는 stdout 으로 Python `marshal` binary 를 흘려 보낸다.
한 record 가 정확히 한 dict 로 풀려 P4Python 의 `run()` 반환값과
1:1 호환.

```python
import marshal, subprocess
def run(args):
    cp = subprocess.run(
        ["p4", "-G", *_conn_flags(), *args],
        capture_output=True, check=False, timeout=None,
    )
    if cp.returncode != 0:
        raise P4Exception(cp.stderr.decode("utf-8", "replace"))
    out, i = [], 0
    while i < len(cp.stdout):
        try:
            rec = marshal.loads(cp.stdout[i:])
        except ValueError:
            break
        # marshal.loads 가 길이를 알려주지 않으므로 BytesIO + load 반복
        ...
    return _decode_strs(out)
```

실제로는 `marshal.load(BytesIO(...))` 반복 호출이 더 안전. 모든
record 가 bytes 키-값이라 `_decode_strs` 가 일괄 utf-8 디코딩.

### 5.2. Form 편집 — stdin 파이프

```python
def update_changelist_description(change, new_desc, force=False):
    form = run(("change", "-o", str(change)))[0]
    form["Description"] = new_desc
    text = _form_dict_to_text(form)
    args = ["change", "-i"]
    if force:
        args.insert(1, "-f")
    subprocess.run(
        ["p4", *_conn_flags(), *args],
        input=text.encode("utf-8"),
        capture_output=True, check=True,
    )
```

`_form_dict_to_text` 는 P4 form 의 표준 텍스트 포맷 (예 :
`Change:\t12345\n\nDescription:\n\t<text>\n`) 으로 변환. 모든 필드를
순서 보존하고 multiline 은 tab indent.

`create_changelist` 도 같은 방식.

### 5.3. Streaming grep — Popen + `-ztag`

```python
def grep_stream(pattern, scope, on_match, cancelled, ...):
    proc = subprocess.Popen(
        ["p4", "-ztag", *_conn_flags(),
         "grep", "-s", "-n", "-i", "-e", pattern, scope],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=1, text=True,
    )
    record: dict = {}
    for line in proc.stdout:
        if not line.strip():
            if record:
                on_match(record)
                record = {}
                if cancelled():
                    proc.terminate()
                    break
            continue
        # "... key value\n"
        if line.startswith("... "):
            key, _, val = line[4:].partition(" ")
            record[key] = val.rstrip("\n")
    proc.wait(timeout=5)
```

`Popen.terminate()` 가 cancellation. P4Python 의 `OutputHandler.
CANCEL` 와 동일 의미.

### 5.4. 연결 인자

`port` / `user` / `client` / `charset` 를 `_conn_flags()` 가 매번
조합 :

```
["-p", port, "-u", user, "-c", client, "-C", charset]
```

미설정 필드는 P4 환경 변수에 위임 (해당 flag 생략).

### 5.5. 예외 매핑

`subprocess.CompletedProcess.returncode != 0` 일 때 stderr 텍스트로
원인 분류 :

| stderr 패턴 | 매핑 |
|---|---|
| "connect to server failed" / "TCP connect to" | `P4ConnectionError` (resilient runner 가 재시도) |
| "Perforce password (P4PASSWD) invalid" | `P4AuthError` (사용자에게 `p4 login` 안내) |
| "no such file(s)" / "no file(s) to" | 일반 `P4Exception` (호출자가 그대로 catch) |
| 기타 | `P4Exception` |

CLI 의 exit code 자체는 1 / 2 정도라 stderr 패턴 매칭이 핵심.
패턴 리스트는 단위 시나리오로 fixture 저장.

## 6. 자원 / 성능 예산

| 단계 | 목표 (로컬 서버) | 한계치 (원격, RTT 80 ms) |
|---|---|---|
| 단발 명령 (`p4 -G info`) | < 100 ms | < 500 ms |
| 작은 dict 결과 ~10 행 | < 150 ms | < 600 ms |
| 큰 dict 결과 ~5000 행 (`files`) | < 1 s | < 5 s |
| streaming grep 첫 매치 | < 300 ms | < 1 s |
| chunked sync 한 chunk (50 파일) | < 2 s | < 10 s |

미달 시 :
- 단발 명령 200 ms 초과 → JobRunner / chunk 크기를 늘려 amortize.
- 큰 dict 결과 5 s 초과 → output을 line-by-line `-ztag` streaming
  으로 변경 옵션 검토.

## 7. 우려 사항 / 미해결

### 7.1. 인증 prompt

CLI 가 만료된 ticket 을 만나면 stdin 으로 password 를 묻는다.
대응 :
- 모든 subprocess 에 `stdin=DEVNULL` 명시 → prompt 가 즉시 실패 →
  stderr "Perforce password invalid" → 우리가 `P4AuthError` 로
  변환 → 사용자에게 toast + Log "외부에서 `p4 login` 후 재시도"
  안내.
- `p4 login -s` 만 stdin 허용 (login 자체는 외부 도구의 영역).

### 7.2. 환경 변수 / 워킹 디렉터리

P4Python 은 connection 시점에 환경을 한 번 캡처. CLI 는 매 호출
마다 현재 환경. 사용자가 작업 중 `P4CONFIG` 위치를 바꾸거나
하면 호출별로 다른 동작 가능. → 시작 시 `p4 set` 으로 effective
값 캡처해 _conn_flags 에 명시적으로 박는다.

### 7.3. 큰 binary 결과 (`p4 print`)

`-G` 의 marshal 은 binary 도 dict 의 `data` 필드 안에 bytes 로
담아 준다. 5 MB 캡 (FileViewer) 이라 메모리 문제 없음.

### 7.3a. `marshal.load` 신뢰 경계

Python 공식 문서는 *"Never unmarshal data received from an untrusted
or unauthenticated source"* 를 명시 — `marshal` 은 신뢰할 수 있는
in-process 직렬화용이지 네트워크 / 디스크 영속화용이 아니다. 본
구현이 `marshal.load(Popen.stdout)` 를 호출하는 이유와 안전 근거:

* **신뢰 경계 = 사용자가 명시적으로 연결한 p4d 서버**. P4Python
  백엔드가 자기 소켓에서 protocol bytes 를 읽을 때 신뢰하는 것과
  같은 경계.
* CPython 의 `marshal` 모듈은 eval-like 동작이 없음 (역사적으로
  몇 번의 메모리 corruption CVE 가 있었으나 임의 코드 실행 경로는
  없었음).
* `_read_marshal_stream` 은 `EOFError` / `ValueError` 를 잡아 "스트림
  끝" 으로 취급 — corrupt tail 로 일부 row 가 사라져도 워커는
  살아남음.
* CL-backend 의 모든 `_invoke` 호출에 per-call timeout
  (`_DEFAULT_CLI_TIMEOUT_S`, default 1800 s, `P4V_CLI_TIMEOUT`
  로 override) 이 걸려 있어 무한 스트림이 hang 으로 이어지지 않고
  `P4Exception` 으로 surface.

만약 누군가 본 모듈의 `_read_marshal_stream` / `grep_stream` 의
marshal 경로에 **외부 입력** (네트워크에서 받은 marshalled blob,
서드파티가 만든 파일, 사용자 입력 등) 을 흘려넣어야 할 일이
생기면, 그 시점에서 `marshal` 을 `json` / `msgpack` 같은 hardened
parser 로 교체해야 한다. `marshal` 은 본 모듈의 p4d-trust 경우에만
적합.

### 7.4. 한국어 / unicode

`p4 -G` 가 charset 변환을 server-side 에서 수행하므로 dict
value 는 이미 utf-8 bytes. `.decode("utf-8", errors="replace")`
일괄. P4Python 의 자동 변환과 동등.

### 7.5. CLI 버전 차이

`p4 -V` 결과로 server / client 버전 출력. 핵심 기능 (`-G`,
`-ztag`, `change -i`, `grep -s -n`) 은 모두 2014+ 에서 안정.
회사 표준 p4 가 2018+ 이라 안전.

### 7.6. Windows

본 사이클에서 함께 지원. `subprocess.Popen` 에 list-form argv 만
넘기므로 shell quoting / path separator 차이는 자동 처리. TTY 깜빡임
은 `creationflags=CREATE_NO_WINDOW` 로 제거 — `p4.exe` 가 GUI
서브시스템 바이너리라 콘솔이 없는 부모에서 spawn 될 때 잠깐
콘솔 창이 떴다 사라지는 현상이 사라진다 (`p4client.py` 의
`_SUBPROCESS_FLAGS` 참조).

## 8. 인터페이스 동결 — 두 backend 가 반드시 동일하게 구현해야 하는 메서드

```
class _Backend:
    def configure(self, *, port, user, client, charset) -> None
    def connect(self) -> None             # CLI 는 no-op
    def disconnect(self) -> None          # CLI 는 no-op
    def is_connected(self) -> bool
    def run(self, *args) -> list[dict | str]
    def run_login_status(self) -> list[dict]
    def fetch_form(self, kind: str, key: str | None) -> dict
    def save_form(self, kind: str, form: dict, *, force=False) -> None
    def grep_stream(self, pattern, scope, on_match, cancelled,
                    *, case_insensitive, max_matches) -> int
    def info(self) -> dict
```

`P4Service` 의 나머지 wrapper 메서드는 위 9개를 조합해 만든다.

## 9. 회귀 테스트 전략

- `tests/backend_parity.py` — 같은 호출을 두 backend 로 실행하고
  결과 dict 의 키 집합 / 값 동일성 비교. CI 에 P4Python 과 CLI
  둘 다 설치된 환경 한 곳 필요.
- Fixture : 작은 mock depot (`mockp4` 또는 실서버의 read-only
  view) 위에서 :
  - `depots` / `dirs //...` / `files //... -m 10` / `info`
  - `fstat //file`
  - `changes -s pending -u <me>`
  - `describe -s <CL>`
  - `filelog -L -m 5 //file`
  - `where //file`
  - `client -o <name>`
- 비교 무시 항목 : `Update` 타임스탬프, `clientHost` 같이 환경
  의존 필드 (parity test 에서 화이트리스트 처리).

## 10. 출하 범위 — Phase A / B / C

### Phase A — 읽기 전용 (이번 사이클)

- [ ] `_Backend` 인터페이스 정의 + `_PythonBackend` 분리 (기능
      변화 0, 코드 이동만)
- [ ] `_CLIBackend.run` (`-G` marshal 파싱)
- [ ] `info` / `depots` / `dirs` / `files` / `fstat` / `where` /
      `pending_changes` / `submitted_changes` / `describe` /
      `filelog` / `opened_in_change` / `fetch_client_view`
- [ ] Backend 선택 로직 (env var + auto)
- [ ] LogPanel 에 startup 한 줄 (`Backend: …`)
- [ ] backend_parity 테스트 일부 (depots / files / fstat)

### Phase B — 쓰기 경로 + form

- [ ] `create_changelist` / `update_changelist_description` /
      `fetch_form` / `save_form` (stdin pipe)
- [ ] `run_login_status`
- [ ] `P4ConnectionError` 분기 + resilient runner 인식
- [ ] `P4AuthError` 분기 + 사용자 toast
- [ ] backend_parity 테스트 확장

### Phase C — streaming + 보조

- [ ] `grep_stream` (`Popen` + `-ztag` line parser)
- [ ] 큰 결과 (`files`) 가 -G 로 5 s 초과 시 `-ztag` streaming
      모드로 자동 전환 (선택)
- [ ] `p4v.py` 의 친절 누락 의존성 메시지에 CLI 안내 추가
- [ ] README "설치" / "백엔드" 섹션 갱신

## 11. 시나리오 합의 — 결정 사항 (확정)

1. **자동 fallback vs 명시 opt-in** → **자동 fallback** 채택.
   `_select_backend()` 가 P4Python 우선 시도 → 실패 시 CLI 로
   자동 전환. 강제 지정이 필요하면 `P4V_BACKEND={python,cli}`
   환경 변수.
2. **성능 회귀 허용 범위** → **수용**. 호출당 ~50 ms 추가는
   ChunkedSyncJob / IndexBuildJob 의 chunk 사이즈로 충분히
   amortize 가능. CLI 모드를 "동등 기능 + 약간 느림" 으로 명시
   (Log 패널 startup 줄에 "p4 CLI" 표시).
3. **Windows 지원 시점** → **본 사이클 포함**. `_CLIBackend` 의
   모든 `subprocess.Popen` / `subprocess.run` 호출에
   `CREATE_NO_WINDOW` 를 적용해 `p4.exe` 의 콘솔 깜빡임을
   억제. argv 는 항상 list 형식이라 shell quoting / path
   separator 차이로부터 자유. macOS / Linux / Windows 모두 동일
   코드 경로.
4. **회귀 테스트 인프라** → **pytest 셋업 추가** (manual smoke
   대신). `pyproject.toml` + `requirements-dev.txt` + `tests/`
   생성. 구성 :
   - `tests/test_p4client_unit.py` — 34 unit 테스트 (marshal /
     form / flatten / project / extract_error). 서버 / `p4` 불필요.
   - `tests/test_p4client_live.py` — 22 parametrized live 테스트
     (양 backend 동일 assertion). `p4 info` 실패 시 자동 skip.
   - `tests/test_p4client_live_crud.py` — `create → fetch →
     update → fetch → delete` CRUD 라운드트립 (양 backend).
     `PYTEST_ALLOW_WRITES=1` 게이트.

---

## 12. 구현 진행 상황

### Phase A — 읽기 전용 ✅ 완료

- [x] `_Backend` 인터페이스 정의 + `_PythonBackend` 분리
- [x] `_CLIBackend.run_tagged` (`-G` marshal 파싱) + `run_text` (untagged)
- [x] `info` / `depots` / `dirs` / `files` / `fstat` / `where` /
      `pending_changes` / `submitted_changes` / `describe` /
      `filelog` / `opened_in_change` / `fetch_client_view`
- [x] Backend 선택 로직 (env var + auto)
- [x] LogPanel startup 한 줄 (`Backend: …`)
- [x] backend_parity 테스트 (22 케이스 × 2 backend = 44 assertion)

### Phase B — 쓰기 경로 + form ✅ 완료

- [x] `_CLIBackend.fetch_form` (`-G change -o` → flatten numbered)
- [x] `_CLIBackend.save_form` (text-form stdin, **`-G` 미사용** —
      구현 도중 `-G change -i` 는 marshalled 바이너리 입력을
      기대해 "Invalid marshalled data supplied as input." 로
      실패함을 확인. §5.2 의 text 방식이 옳음.)
- [x] `create_changelist` / `update_changelist_description` —
      façade 가 fetch_form + save_form 으로 위임
- [x] `run_login_status` — 비-retry
- [x] CRUD 라운드트립 테스트 (`test_p4client_live_crud.py`)
- [ ] `P4ConnectionError` / `P4AuthError` 분기 — **유보**. 현재
      `_is_connection_error()` 의 substring 매칭으로 retry-vs-fail
      이 둘 다 정확. 사용자 측 인증 에러 대응 (`p4 login` toast)
      는 후속에서 다룬다.

### Phase C — streaming + 보조 ✅ 완료

- [x] `_CLIBackend.grep_stream` — `Popen` + marshal stream parser.
      (시나리오는 `-ztag` text 모드를 제안했으나, `-G` 마샬
      스트림 파싱이 `run_tagged` 와 대칭이고 line parser 가
      필요 없어 더 깔끔. 결과 동일.)
- [x] `p4v.py` 친절 누락 의존성 메시지 — `_print_no_backend`
      추가. P4Python 힌트도 "CLI 가 대안" 문구로 갱신.
- [x] `requirements.txt` — `p4python` 에 "선택" 코멘트.
- [x] `requirements-dev.txt` 신규 — `pytest>=7.0`.
- [x] `README.md` — "Perforce 백엔드" 절 추가 (선택지 표 +
      auto 선택 규칙 + `P4V_BACKEND` 강제 지정 예시).
- [x] `DESIGN.md` — Architecture 의 "Backends" 절 + 히스토리
      "Backend split" 노트.

### 후속 — 미스코프

- `P4AuthError` UI : 만료 ticket 감지 시 "외부에서 `p4 login`"
  toast + Log 안내. 현재는 일반 P4Exception 으로 표시.
- 큰 dict 결과 (`files -m 50000`) `-G` 응답이 5 s 초과 시
  `-ztag` streaming 으로 자동 전환 — 측정해 필요해지면 추가.
- **True connection 재사용** (한 TCP 세션 → 다중 명령) : `p4` 바이
  너리에는 REPL / arbitrary-command stdin 모드가 없음 (`p4 -s` 는
  per-line tag 출력일 뿐). 진짜 재사용은 (a) P4Python 자체 사용,
  (b) `p4-broker` 외부 multiplexer 설치, (c) p4 wire protocol Python
  재구현 — 세 가지 모두 CLI-fallback 의 "p4 바이너리 하나면 OK"
  의도에 맞지 않음. 본 사이클에서 **시도하지 않음**.

### 16. CLI backend — 동시성 + idempotent-read 캐시 (option B 절충안)

True connection 재사용 대신 구현한 perf 개선:

* **동시 subprocess 실행** : 기존 `P4Service._lock` 단일 mutex 를
  `_connect_lock` (connect/disconnect 짧은 mutex) + `_call_sem`
  (BoundedSemaphore(N)) 두 단계로 분리.
  - Python backend : N=`P4V_PY_CONCURRENCY` (기본 4). 단일 `P4.P4()`
    소켓은 thread-safe 하지 않으므로, **독립 연결 풀**(스레드당 P4
    인스턴스 1개 — P4Python 이 지원하는 패턴)을 두고 호출마다 하나씩
    리스한다(`_PyConn` / `_acquire` / `_release` / `_connection`).
    이전엔 N=1 로 단일 연결을 직렬화했는데, 그 결과 느린 명령 하나가
    다른 모든 p4 호출을 막아 앱이 멈춘 것처럼 느껴졌다(CL 57599 에서
    풀로 전환해 수정). `1` 로 두면 과거의 직렬 동작으로 복귀.
  - CLI backend : N=`P4V_CLI_CONCURRENCY` (기본 4) — 각
    subprocess 가 독립이므로 병렬 실행 안전. Tree expand 시 fan-
    out 된 `dirs` + `files` + `fstat` 가 직렬화되지 않고 동시에
    진행돼 cold-cache UI 응답이 체감 빨라짐.
  - 즉 두 backend 모두 *독립 연결*(Python=풀, CLI=subprocess)로
    동시성을 얻으며, 공유 연결은 쓰지 않는다. 느린 명령 하나는 자기
    연결 하나만 점유하므로 UI 반응성이 유지된다.
  - `_Backend.max_concurrent_calls` 클래스 변수로 각 backend 가
    자기 동시성 수준을 선언, façade 는 그대로 받아서 BoundedSem
    크기로 사용.

* **Idempotent-read TTL 캐시** (`_CLIBackend._read_cache` /
  `_CACHEABLE_ARG_HEADS` / `invalidate_read_cache`) : `info` /
  `client -o <name>` 같은 자주 호출되지만 잘 안 바뀌는 명령
  결과를 `P4V_CLI_READ_CACHE_TTL` (기본 30 s) 동안 캐싱. UI 가
  여러 곳에서 같은 spec 을 fetch 해도 subprocess 한 번. `save_form`
  (즉 `client -i` / `change -i`) 이 한 번 돌면 캐시 전체 flush
  — 부분 invalidate 매핑보다 cost 낮음.

* 한계 (정직하게) :
  - 호출당 latency (50-100 ms 로컬 서버 spawn 비용) 는 그대로.
    감소시키려면 진짜 connection 재사용 필요.
  - 캐시는 read-only 명령에만 적용 — 쓰기 경로는 영향 없음.
  - 캐시 TTL=0 으로 비활성화 가능 (`P4V_CLI_READ_CACHE_TTL=0`).
