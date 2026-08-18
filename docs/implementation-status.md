# 구현 상태와 복구 체크포인트

마지막 갱신: 2026-08-18 KST  
현재 phase: **Research Peer 2.0.2 session-scoped rp auto-answer source, local automated verification, and user-scope install complete; external Claude/peer acceptance pending**

session compaction/restart 뒤 이 파일을 먼저 읽는다. 제품 요구사항은 `docs/product-spec.md`, 설계 결정은 `docs/architecture.md`, 안전 경계는 `docs/security-model.md`, 검증 matrix는 `docs/test-plan.md`가 authority다. 현장 근거는 `docs/field-report-2026-08-18.md`다.

## 2.0을 촉발한 현장 근거

- **[SERVER-VERIFIED]** 2026-08-18 서로 다른 두 university lab server/account 사이의 첫 실제 pairing이 완료됐다.
- **[SERVER-VERIFIED]** 양쪽 host는 default-drop inbound firewall이고 owner에게 sudo가 없었으며 SSH ingress만 가능했다. 한 owner-managed SSH connection의 local/reverse forwarding 뒤 양방향 TLS P2P가 성공했다.
- **[SERVER-VERIFIED]** transport, certificate pin, application signature, ACK, QUESTION/ANSWER request correlation은 정상 동작했다.
- **[SERVER-VERIFIED]** setup 중 unreadable inbox, phantom session, multi-session broadcast suppression, stale daemon listener, wildcard invite, 30분 invite expiry, false-clean doctor가 재현됐다.

## 2.0 구현 완료

- `receive`를 public help에서 제거하고 hidden `_ingest`로 대체; TTY 입력은 명시적 오류
- `research-peer inbox [--room] [--all] [--consume]`와 inbound body/metadata 조회
- `research-peer history [--room]`로 inbound/outbound/automatic reply body와 disclosure/depth audit
- `session register`가 `RESEARCH_PEER_SESSION_ID` 우선 사용; 없으면 phantom 가능성 경고
- 같은 room/alias 재등록 시 이전 binding retire, stale session 자동 비활성화/current 표시
- inbound broadcast/exact alias를 수신 시점의 가장 최근 live session에 durable assignment; no-target message는 후속 registration이 claim
- `init` warning, `start` refusal, `status`/`doctor` 비교를 통한 config/live listener mismatch 가시화
- advertised wildcard 거부; loopback은 established tunnel용 `--advertise-loopback` 명시 필요
- invite 기본 TTL 24시간, human-readable remaining validity, `INVITE_EXPIRED` code
- doctor의 read-only UFW/default-drop heuristic과 explicit reverse-bind SSH remediation
- `room status`의 peer fingerprint/endpoint, session, message count, last exchange
- daemon log에 direction/remote/room/message/sender/type/stable error metadata 추가; message body/secret 제외
- room별 auto-answer default off, disclosure `none|status|summary|full`, owner note
- dedicated MCP answer tool: QUESTION/reply_required/owner_attention/policy/depth/one-request-one-answer 강제
- non-QUESTION `reply_required=true` protocol rejection; automatic QUESTION 금지와 terminal ANSWER instructions
- `/research-peer:auto-answer` owner-only skill과 make/join tunnel/listener guidance
- `/research-peer:update` owner-only skill과 fixed official GitHub self-updater; release identity/version 검증, downgrade 거부, state 보존, conditional daemon restart
- `/research-peer:make`와 `join`의 no-prerequisite guided onboarding: prompt 뒤 같은 workflow 재개, direct/tunnel 선택, endpoint/daemon 자동 조정, session binding, 연결 후 auto-answer opt-in 질문
- 인자 없는 `rp`의 session-only full auto-answer opt-in, `--no-auto-answer`, assigned live-session enforcement, room policy precedence
- SQLite schema v2 migration: room policy, durable session assignment, auto-answer audit
- package/Python/plugin/marketplace version 2.0.2

## 유지되는 1.x 기능

- direct TLS TCP + certificate fingerprint pin + ECDSA-signed envelope
- strict protocol/HANDOFF schema, replay/dedup/rate limits
- SQLite WAL outbox, durable ACK, exponential retry, daemon recovery
- Channel provenance, permission relay 없음, user-scope installer/uninstaller safety
- `rp` session-scoped Remote Control/full auto-answer opt-in과 canonical opt-in-off `research-peer`
- room leave/delete exact local scope와 project/artifact preservation

## 검증 결과

- **[SERVER-VERIFIED]** Python unittest 43 pass: protocol terminal reply, room/session auto-answer policy, assigned live-session enforcement, inbox/history, latest-session assignment, late claim, alias retire, listener mismatch, environment session binding, endpoint validation, 24-hour invite, guided make/join skill contract, installer/uninstaller와 isolated updater 포함
- **[SERVER-VERIFIED]** isolated 2-peer TLS HANDOFF/QUESTION/ANSWER/retry + policy-limited auto-answer/request-id audit E2E pass
- **[SERVER-VERIFIED]** Node test 4 pass: permission relay 없이 `research_peer_send`, `research_peer_answer`, `research_peer_status` 세 tool과 actual stdio handshake 검사
- **[SERVER-VERIFIED]** installer test는 새 `auto-answer` skill ownership/removal을 포함
- **[SERVER-VERIFIED]** updater test는 `2.0.2 → 2.0.3` local candidate 적용, component version mismatch/downgrade 거부, identity와 room state 보존을 확인
- **[SERVER-VERIFIED]** Ruff 0.12.9, Python compileall, strict root/plugin validation pass; npm production audit vulnerabilities 0
- **[SERVER-VERIFIED]** sanitized `dist/research-peer-2.0.0.tar.gz` isolated clean install → 14 skills와 `help update`/strict plugin 확인 → default uninstall residue-none pass
- **[SERVER-VERIFIED]** 현재 user scope에 2.0.2 reinstall: canonical/`rp` version 2.0.2, plugin skills 14, installed `start --remote-control --auto-answer` delegation과 session env enforcement, daemon 실행/listener 일치 확인
- **[OFFICIAL]** Claude custom Channel은 여전히 research preview이며 session-start development Channel confirmation과 organization policy에 종속
- NOT YET TESTED: 실제 Claude에서 2.0.2의 multi-turn make/join onboarding과 `rp` session opt-in unattended ANSWER 생성
- NOT YET TESTED: published official GitHub의 newer release를 실제 `/research-peer:update`로 clone/apply
- NOT YET TESTED: 2.0으로 외부 두 server 재-pair 및 tunnel 장기 유지
- NOT TESTED: Remote Control mobile/browser session 또는 push behavior

## 의도적으로 후속으로 남긴 항목

- `research-peer connect` verification-first wizard
- `research-peer verify ROOM` daemon/session round trip
- `request-info`/`accept-info` structured peer-prerequisites exchange
- Research Peer 자체의 SSH tunnel process/key lifecycle 관리

2.0은 이 항목들을 구현했다고 주장하지 않는다. 대신 관찰된 pure implementation bugs를 수정하고 exact manual SSH recipe와 diagnostic을 제공한다. SSH key 설치, fingerprint confirmation, firewall/sshd 변경은 계속 human trust decision이다.

## Known limitations와 잔여 위험

- custom Channel warning은 local owner가 확인해야 하며 installer가 자동 승인하지 않는다.
- broadcast는 모든 session에 복제하지 않고 수신 시점의 가장 최근 live session 하나를 선택한다.
- `full` auto-answer는 명시적 opt-in이어도 model disclosure 오판 위험이 있으므로 status/summary가 권장된다.
- sender `sequence` reorder buffer/gap retransmission은 없다.
- SSH forwarding은 문서화된 owner-managed network path이며 daemon이 직접 supervise하지 않는다.
- workspace `.git`이 read-only placeholder인 환경에서는 commit/publish를 수행하지 않는다.

## 다음 검증 명령

```text
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
npm test
uvx ruff@0.12.9 check src tests
npm audit --omit=dev
claude plugin validate . --strict
claude plugin validate plugin --strict
```

실제 owner acceptance 전에는 `research-peer status`, `research-peer doctor`, `research-peer inbox`, `research-peer room status ROOM`을 먼저 확인한다. credential/private key/password/token은 요청하거나 기록하지 않는다.
