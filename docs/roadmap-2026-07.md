# p4v-tui — 다음 단계 검토 (2026-07)

> 2026-07-10, 전체 문서(README / DESIGN / docs/*) · 코드 트리 ·
> depot 체인지리스트 300건(CL 50183 → 60268)을 다시 읽고 "앞으로 무엇을
> 해야 하는가"를 정리한 검토 문서. 우선순위 판단의 근거를 함께 남긴다.
>
> 갱신 규칙: 항목이 닫히면 이 문서의 체크박스를 갱신하고, 기능 커버리지
> 변화는 늘 그랬듯 `DESIGN.md` 매트릭스 → `docs/p4v-feature-gaps.md`
> 순서로 먼저 반영한다.

---

## 1. 현재 위치 — 무엇이 끝났는가

한 문단 요약: **만들기로 한 것은 사실상 다 만들었다.** 이제 남은 일은
"기능 추가"가 아니라 **공개 릴리스, 검증 잔여분 소화, 의도적으로 미룬
항목의 재결정**이다.

- **p4v 일상 개발 루프 커버리지 완료.** DESIGN.md 매트릭스 기준
  sync / edit / submit / revert / reconcile(인터랙티브 픽커) /
  branch-copy-integrate / resolve(인앱 3-way + 외부 P4Merge) / shelve
  풀사이클 / annotate / time-lapse / revision graph / undo / diff 전
  형태 / Get Revision / Find File / Preferences GUI까지 ✅. 남은 ❌는
  Jobs 하나뿐이고 나머지는 전부 의도적 ⏭ (admin/spec 편집, 인증 UI).
- **1차 가치(회복탄력성) 완료 + 검증.** R1–R5 로드맵(재접속·청크·재개·
  lost-ack 서브밋) 완료, 백엔드 이원화(P4Python/CLI) + 라이브 패리티
  테스트, CLI 타임아웃·동시성·읽기 캐시 하드닝(52627–52675)까지.
- **2차 가치(원격 사용성) 완료.** narrow 단일 페이지 내비게이터
  (58760–58792, 실기기 iPhone Blink 검증 포함)와 체감 성능 레이어
  (58773–58786), 그리고 6월 말 UI 동결/레이아웃 흔들림 버그 3건 수정
  (60264–60267).
- **검증 부채 대부분 상환.** 3-way merge와 permalink move-following은
  라이브 검증에서 *실제로 깨져 있던 것을* 찾아 고쳤고(56820, 56845),
  p4 action 문자열 매칭 버그류를 전수 감사로 소탕(59308–59388).
  테스트 스위트 575 passing, 헤드리스 e2e(gestures/narrow/perf) 포함.
- **보안 감사 완료, 조치 일부 완료.** HEAD 소스 정화는 CL 57176에서
  끝났고(A2), filter-repo 입력물은 `docs/migration-scrub/`에 준비됨.

마지막 실질 작업이 2026-06-22(60268)이므로, 지금은 "다음 마일스톤을
고르는" 시점이다.

---

## 2. P0 — GitHub 공개 릴리스 ~~를 실행한다~~ → 이미 실행됨, 미러 sync가 실작업

**정정 (2026-07-10 재검토):** 이 절의 전제("실행만 안 된 목표")가
틀렸다. 공개는 **2026-06-07에 이미 실행**됐다 —
[neoocean/p4v-tui](https://github.com/neoocean/p4v-tui) PUBLIC,
`scripts/sync-to-github.sh`(scrub-on-export 미러, `docs/mirror-workflow.md`)
경유. `github-migration-and-deployment.md`의 옵션 1/2 논의는 이 미러
방식(단일 커밋 + 내보내기 시 정화)으로 이미 해소된 과거 검토였다.
이 로드맵의 첫 작성이 mirror-workflow.md를 누락해 상태를 오판했다.

- [x] ~~옵션 결정~~ — **scrub-on-export 미러로 기결정·기실행** (CL
      57217 도구, 첫 push 2026-06-07). Perforce는 내부 참조를 정확하게
      유지하고, 공개 트리는 내보내는 순간 정화된다.
- [x] 공개 전 최종 체크리스트(문서 §F) — 2026-07-10 재검증: 미러 git
      **전체 히스토리**에서 민감 파일(p4v-tui.toml / shared-state/ /
      .claude/ / 감사 문서 / scrub denylist) 0커밋, dry-run 스테이징
      트리에 실식별자 grep 0건(placeholder 제외), gitleaks 스캔.
- [x] **README Deploy 절 + 영문 README** — CL 64026: README.md 영문
      신설(Deployment 절 포함), 한글본 README.ko.md 분리.
- [x] depot ↔ git 동기화 방향 — `docs/mirror-workflow.md`가 이미 정의
      (Perforce가 원본, GitHub는 one-way scrubbed derivative).
- [ ] **실 push (`sync-to-github.sh sync`)** — 2026-06-07(CL 57328)
      이후 미러가 한 달치 뒤처짐. dry-run + 검증 grep까지 준비 완료
      상태이며 push만 사용자 확인 후 실행. ⚠ 주의: 셸 환경에
      `SYNC_GITHUB_REMOTE`가 자매 프로젝트(docker-monitor) URL로 설정돼
      있다 — push 자체는 미러의 `origin`(p4v-tui) 고정이라 안전하지만,
      새 미러를 `init`할 때는 반드시 env를 재설정할 것.

## 3. P1 — 수동 검증 잔여분을 소화한다 → 2026-07-10 자동화 완료 (시각 2건 제외)

**근거:** `docs/handoff-manual-tests.md`에 미체크로 남은 항목들.
"자동화 가능하면 자동화 우선" 원칙대로 전부 회귀 테스트로 전환했다
(CL 64014 / 64021). 남은 것은 헤드리스로 불가능한 시각·실기기 확인
2건뿐.

- [x] **Submit guards** — e2e 자동화 (`test_e2e_submit_guards.py`):
      unresolved+oversized ⛔/⚠ 표기, 빈 CL 블록, 깨끗한 CL 무경고,
      원격 CL 거부 게이팅까지.
- [x] **Partial shelve** — e2e 자동화 (`test_e2e_shelve_bulk.py`):
      부분 선택 → 명시적 파일 인자, 전체 선택 → 인자 생략(통짜 셸브).
- [x] **트리 다중선택 벌크** — e2e 자동화 (같은 파일): 벌크 edit 단일
      호출 + 새 numbered CL 격리, 벌크 revert 확인창 1회 + 전체 목록.
- [x] **공유 상태 machine-B 편집 케이스** — gated 라이브 테스트
      (`test_shared_state_live.py::…readonly_edit…`): read-only 사본에
      atomic temp+replace 쓰기 → reconcile이 **edit**로 잡힘, 양 백엔드.
- [x] **Jira-at-submit** — e2e 자동화: 키 有(🔗+URL) / 無(⚠) /
      설정 無(무음) 3상태.
- [x] **Fast Search 행 액션(d/g)** — e2e 자동화
      (`test_e2e_search_actions.py`): 시드 SQLite 인덱스 픽스처 +
      Input 포커스 게이팅까지.
- [x] `docs/handoff-manual-tests.md` 갱신 — 남은 수동 항목만 표시.
- [ ] **Merge editor 시각 확인** — 헤드리스로는 못 보는 렌더 가독성.
      (진짜 눈 확인만 남음.)
- [ ] **폰 실기기 narrow 레이아웃 확인** — iPhone Blink에서 페이지
      내비게이터/브레드크럼 눈 확인. (58790/58792에서 한 차례 실기기
      검증됨 — 회귀 확인용.)

## 4. P2 — 의도적으로 미룬 기능의 재결정

지금 당장 만들 것이 아니라 **ship / decline을 명시적으로 결정**할
후보들. 결정 결과는 DESIGN.md 매트릭스에 반영한다.

- [x] **Jobs 최소 지원 → ⏭ decline 확정 (2026-07-10).** 판단 기준이던
      "실사용 서버에서 job을 쓰는가"를 조사한 결과: 총 7건, 전부
      closed, open 0건, 마지막 활동 2025-02 (개인 태스크-통합 실험
      후 중단). 실수요 없음 → DESIGN.md 매트릭스의 ❌ 2행(fix 연동
      / job 검색)을 ⏭로 격하, `p4v-feature-gaps.md`·README 동기화.
      ❌ 분류는 이제 매트릭스에 0건. jobs 쓰는 서버가 실타깃이 되면
      "읽기 전용 픽커 + `p4 fix -c`" 최소 표면으로 재개.
- [ ] **CL 테이블 path 필터** — 남은 유일한 필터 갭. per-CL `describe`
      비용 때문에 미룸. 청크드 백그라운드 describe + 캐시로 가능은
      하나, 실수요가 확인될 때까지 보류 권고.
- [ ] **Custom Tools(선택 항목에 임의 명령 실행)** — `[[macro]]` +
      `[[external_editor]]`가 대부분 커버. "커서 경로 치환 인자"만
      macro에 추가하면 격차가 거의 사라짐 — 저비용이라 검토 가치 있음.
- [ ] **P2.3 대형 리스트 렌더 비용** — 시나리오 문서상 "측정 먼저".
      구형 폰 + 수백 행 folder-history로 한 번 측정해서 닫거나 열자.

**재확인하는 decline (다시 검토하지 않음):** P2.2 취소 가능 로드
(스레드 워커가 블로킹 p4 소켓을 중단 못 함 — 구조적), admin/spec
편집 표면 전체, Login/SSO/Tickets UI (보안 경계는 `p4` CLI 한 곳).

## 5. P3 — 테스트·문서 부채 (작고 명확한 것들)

- [x] **CLI grep watcher kill 단위 테스트** — CL 64024
      (`test_cli_grep_watcher.py`): fake Popen + 실제 OS 파이프로
      "marshal.load 블록 중 cancel → watcher가 kill" 을 결정적으로
      고정. 정상 EOF/디코드 경로 + max_matches 캡도 함께.
- [x] **CLAUDE.md 최신화** — CL 64009 (수동 잔여 목록 교체) + CL 64026
      (README 영문/한글 분리 반영).
- [x] **DESIGN.md CL 이력 절 다이어트** — CL 64060: ~500줄 서사를
      `docs/changelog-archive.md`로 verbatim 이관, DESIGN.md에는
      R-series 표 + 배치별 1행 타임라인 인덱스만 (1,052→591줄).
      P3 전량 소진.
- [x] **테스트 러너 상시 확인** — 2026-07-10 기준: 586 passed / 6
      skipped(gated), `PYTEST_ALLOW_WRITES=1` 라이브 write 4 passed,
      ruff -F clean (p4v_tui + tests).

---

## 6. 권장 실행 순서 → 2026-07-10 실행 결과

같은 날 자율 세션에서 P1·P2·P3 및 P0 준비를 실행했다 (CL 64008 /
64009 / 64014 / 64021 / 64024 / 64026 + 본 문서 갱신):

1. ~~P1 검증 세션~~ → **자동화로 상향 완료** — 수동으로 남은 것은
   시각 확인 2건(§3)뿐.
2. ~~P0 옵션 1 공개~~ → **이미 공개돼 있었음**(§2 정정). 남은 실작업은
   `sync-to-github.sh sync` 1회 (dry-run + 검증 grep + gitleaks 완료,
   push만 사용자 확인 대기).
3. **P2 재결정 완료** — Jobs decline 확정(§4). **P3**는 DESIGN.md
   다이어트 1건만 잔존(§5).

갱신된 핵심 판단: 릴리스는 이미 실행돼 있었고, 남은 병목은 **미러
sync 주기화**다 — 한 달치 지연이 보여주듯 공개 저장소의 신선도는
사람이 기억해야 하는 수동 단계에 걸려 있다. 다음 개선 후보:
서브밋 후 훅 또는 주기 작업으로 `sync-to-github.sh sync` 자동화.
