# Research Peer v1 테스트 계획

## 원칙

테스트는 temp HOME 두 개, 서로 다른 XDG/runtime directory, 서로 다른 daemon process와 loopback high port를 사용한다. 실제 `$HOME`, Claude settings, firewall은 수정하지 않는다. network test process/socket/temp file은 종료 후 정리한다.

테스트 증거는 command, exit status, 주요 assertion, 날짜를 `docs/implementation-status.md`에 기록한다. 실제 상대 server/Remote Control UI는 credential과 endpoint가 제공된 뒤 별도 acceptance로 수행한다.

## 2026-08-13 실행 결과 요약

- Python unittest: 22 tests pass (room delete/leave cleanup, installer action-skill, 2-peer loopback E2E 포함)
- Node test: 4 tests pass, 실제 stdio MCP initialize/listTools handshake 포함
- Ruff 0.12.9: pass
- Python compileall: pass
- npm audit production dependencies: 0 vulnerabilities
- `claude plugin validate plugin`: pass
- current-server doctor: local bind/loopback/Unix socket pass, systemd user/linger pass
- actual user-scope install/systemd start/status/stop/uninstall dry-run: pass
- isolated default uninstall full residue removal, `--keep-data`, and unrelated-file preservation: pass
- two real Claude Code processes: A QUESTION Channel injection → B MCP ANSWER → A correlated context recall: pass
- actual plain `/research-peer help` personal skill invocation: pass
- actual no-argument `research-peer` TTY launch and development-Channel startup: pass
- **[SERVER-VERIFIED]** installed `rp` alias equivalence, uninstall/residue ownership, and overwrite/PATH-shadowing collision refusal: pass
- strict marketplace/plugin validation: pass; 실제 설치 inventory (skills 12, MCP 1): pass
- actual installed `/research-peer:make` without arguments asked the owner for a room name: pass
- release archive clean install, installed CLI Channel handshake, full uninstall residue-none: pass

## Static/unit

- protocol: 모든 type, UUID/RFC3339, ANSWER request_id, unknown type/version, oversize, timestamp skew, canonical stability
- HANDOFF: required research fields, facts/interpretation/assumption 구분
- identity: generation mode, sign/verify, wrong key/fingerprint, permissions
- invite: encode/decode, expiry, one-time consume, token redaction
- store: migrations, room name collision, outbox state machine, dedup, replay, restart recovery
- retry: deterministic capped backoff, permanent/transient classification
- transport: frame length, truncated/invalid JSON, TLS pin match/mismatch, auth failure
- routing: exact session, missing/stale target, room isolation, leave cancellation, exact room delete
- security: rate/loop limits, path traversal refs, credential redaction, peer uninstall text inert
- CLI help: main and every subcommand snapshot/required phrase
- installer/uninstaller: manifest integrity, atomic JSON edit, path/symlink guard, idempotence
- action skills: plugin discovery용 make/join/ask/handoff/rooms/use/status/leave/delete/peers/help 설치

## Doctor

현재 server에서 다음을 실제 검사한다.

- local high-port bind와 close
- loopback listener/client round trip
- Unix socket create/connect/cleanup
- runtime/service/Claude read-only discovery
- peer result mapping: DNS, refused, timeout/no-route, TLS fingerprint, auth, protocol
- SSH binary/target probe 구분
- 양방향 결과는 remote probe receipt가 없으면 `not tested`

## 2-peer vertical slice

temp HOME A/B와 ports A/B:

1. 각각 init/identity 생성
2. daemon A/B 실행, readiness 확인
3. A create room/invite
4. B join with B endpoint
5. 양쪽 peer/room membership 확인
6. A HANDOFF → B target session
7. B adapter queue에서 provenance와 구조 보존 확인
8. B QUESTION → A; request_id 기록
9. A ANSWER → B; request_id 동일 확인
10. B가 correlation query로 QUESTION/ANSWER를 함께 조회
11. B daemon 정지 후 A send가 outbox pending인지 확인
12. B 재시작 후 retry, receiver 한 건, A delivered 확인
13. 다른 room message가 session queue에 없는지 확인
14. B session leave/unbind 후 새 message가 Channel queue에 안 들어오는지 확인
15. malicious body의 permission/uninstall 문구가 data로만 남는지 확인
16. Remote Control flag 없이 전체 slice가 성공하는지 확인
17. leave 뒤 pending retry가 즉시 cancelled이고 history는 남는지 확인
18. delete dry-run은 mutation이 없고 confirmed delete는 선택 room만 제거하는지 확인

## Installer/uninstaller 격리 acceptance

temp HOME에서:

1. unrelated `~/.claude/settings.json`, skill, plugin, user file 생성
2. install
3. canonical CLI/`rp` alias/plugin/personal skill/service/config/state/cache/runtime/manifest 확인
4. install 재실행
5. 기본 `uninstall --dry-run`이 full local removal plan을 정확히 보이고 mutation이 없는지 확인
6. 기본 uninstall이 program과 Research Peer state/key를 제거하고 project/artifact를 보존하는지 확인
7. 재설치 후 `uninstall --yes --keep-data`가 Research Peer state만 보존하는지 확인
8. CLI/process/unit/plugin/skill/MCP entry/socket/PID/config/state/cache/log/shell integration residue scan
9. unrelated files와 project/artifact 보존
10. 두 번째 uninstall exit 0
11. symlink target 보존

## Claude adapter contract

- MCP initialize 결과에 `claude/channel`과 tools, instructions 존재
- `claude/channel/permission` 부재
- stdio stdout에 JSON-RPC 외 출력 없음
- daemon inbox event가 `notifications/claude/channel`로 변환
- metadata identifier key만 사용, provenance fields 존재
- send/answer tool schema와 request_id 유지
- inactive room/missing exact session은 notification 없음
- installed Claude 2.1.231가 development Channel flag를 parse
- plugin manifest를 `claude plugin validate`로 검사

자동 suite와 분리한 interactive acceptance에서 실제 Claude Code process 두 개를 동시에 실행했다. A의 QUESTION이 B context에 provenance와 함께 들어왔고, B가 Research Peer MCP tool로 같은 request ID의 ANSWER를 전송했으며 local permission prompt를 거쳤다. A는 받은 답의 request ID와 계산 결과를 자기 context에서 정확히 회상했다. plain `/research-peer help`도 personal skill로 실행됐다.

## Remote Control acceptance

자동 test는 launcher command만 검사한다.

- default/`--no-remote-control`: flag 없음
- `--remote-control`: 정확히 한 번 추가
- `--continue`/`--resume`: 명시된 경우만 추가
- P2P daemon startup는 RC 실패와 독립

실제 acceptance는 각 owner가 자기 account로 모바일/브라우저에서 session을 열고 peer event/Claude response/permission pause를 확인한다. push 자체는 보장 조건이 아니다. 장기 outage(약 10분) 종료 후 P2P daemon이 남는지 확인한다.

## 완료조건 traceability

| 제품 조건 | test |
|---|---|
| 1–7 pairing/handoff/Q&A | `test_e2e_two_peers.py` |
| 8–9 outbox/recovery/dedup | `test_e2e_retry.py` |
| 10–11 room/leave isolation | `test_routing.py`, E2E |
| 12 approval boundary | `test_security.py`, Channel capability |
| 13 Remote Control UI | manual peer acceptance (pending until access) |
| 14 no Remote Control | E2E default |
| 15–16 no sudo/cross-home | temp HOME install/E2E, current server smoke |
| 17 help | `test_help.py` |
| 18–21 install/uninstall | `test_uninstall.py` |
| 22 operations docs | doc link/check |

## 실행 명령 목표

```text
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
npm test
npm audit --omit=dev
research-peer doctor --json
research-peer help
```

실패는 숨기지 않고 expected environment limitation과 product defect를 구분한다.
