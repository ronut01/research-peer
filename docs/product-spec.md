# Research Peer v1 제품 사양

상태: v1 정식 기준선  
기준일: 2026-08-13  
제품명/CLI/skill: **Research Peer** / `research-peer` / `research-peer`

## 증거 표기

- **[SERVER-VERIFIED]** 이 연구 서버에서 읽기 전용 명령 또는 로컬 테스트로 직접 확인한 사실
- **[OFFICIAL]** 2026-08-13 현재 Anthropic 공식 문서나 설치된 Claude Code CLI에서 확인한 외부 기능
- **[ASSUMPTION]** 구현을 위해 채택했지만 실제 상대 서버에서는 아직 검증하지 않은 설계 가정

## 목적과 사용자 시나리오

Research Peer는 서로 다른 Unix 사용자 또는 연구 서버에서 실행되는 두 Claude Code 세션이 중앙 relay 없이 인증된 연구 handoff와 후속 질문을 교환하도록 한다. 현재의 `팀원 Claude Code → 팀원 → 나 → 내 Claude Code` 전달 과정에서 실행 조건, seed, hyperparameter, 실패 시도, raw log, 코드 변경, metric/aggregation, 해석 주의점, 사실과 추측의 경계가 빠지는 문제를 줄인다.

목표 흐름은 `팀원 Claude Code ↔ 내 Claude Code`다. 후속 실험 세션은 handoff를 읽고 부족한 정보를 판단하여 원 세션에 직접 QUESTION을 보낸다. 원 세션은 기존 conversation context와 자기 계정으로 접근 가능한 코드·로그를 확인하고 ANSWER를 보낸다. 후속 세션은 같은 `request_id`로 답을 원 질문과 연결해 실험에 반영한다.

## v1 범위와 비범위

v1은 다음을 제공한다.

- 서로 다른 사용자/서버 간 텍스트 메시징
- 구조화된 experiment HANDOFF, QUESTION/ANSWER, ARTIFACT_REF, STATUS, ACK
- room별 격리, UUID 내부 식별자, 표시 이름 중복 허용
- invite 기반 최초 pairing, identity fingerprint 확인, sender allowlist
- 중앙 relay 없는 인증·암호화 P2P
- SQLite local outbox, ACK, 지수 backoff, 재시작 복구, 중복 제거
- 특정 Claude session과 활성 room 하나의 명시적 binding
- 실행 중 Claude context로 provenance가 표시된 inbound Channel event 주입
- outbound MCP tools, CLI, personal `/research-peer` skill
- user-scope 설치, doctor, 시작/중지, 상태, 로그, 안전 제거
- release archive/Git 기반 팀 배포와 Claude plugin marketplace catalog
- 같은 물리 서버의 다른 Unix 사용자와 다른 물리 서버
- 선택적 Remote Control launcher; Remote Control과 P2P는 독립

v1은 Codex adapter, 중앙 discovery/relay, 3명 이상 최적화, binary artifact 자동 복제, 전체 transcript 자동 공유, peer의 home 접근, 자동 firewall/SSH key 변경을 제공하지 않는다. protocol, identity, room, transport, persistence, retry/outbox는 Claude Code에 의존하지 않으며 향후 adapter를 병렬 추가할 수 있어야 한다.

## 핵심 사용자 경험

### CLI

다음 명령은 v1 공개 인터페이스다.

```text
research-peer help [COMMAND]
research-peer doctor [--peer HOST:PORT] [--ssh-target TARGET]
research-peer init
research-peer start [--room ROOM] [--remote-control|--no-remote-control]
research-peer stop
research-peer status
research-peer room create NAME
research-peer room make NAME
research-peer room join INVITE
research-peer room list
research-peer room leave ROOM
research-peer room delete ROOM [--dry-run] [--yes]
research-peer peer list
research-peer session list
research-peer session register
research-peer send
research-peer receive
research-peer logs
research-peer version
research-peer uninstall [--dry-run] [--keep-data] [--yes] [--purge]
```

**[SERVER-VERIFIED]** `rp`는 `research-peer` 하위 명령과 인자를 그대로 전달하는 짧은 CLI launcher다. 유일한 기본 동작 차이는 interactive TTY에서 인자 없이 실행한 `rp`가 `start --remote-control`로 위임하는 것이다. 인자 없는 canonical `research-peer`는 Remote Control off를 유지하며 `rp start --no-remote-control`도 지원한다. 현재 연구 서버의 설치 전 PATH에는 기존 `rp` command/alias/function이 없었다. **[OFFICIAL]** 이름 자체는 완전히 전용이 아니며 [Homebrew의 ROP 분석 도구](https://formulae.brew.sh/formula/rp)와 [FreeBSD Ports의 Rosenpass 도구](https://man.freebsd.org/cgi/man.cgi?manpath=FreeBSD+Ports+15.0&query=rp&sektion=1)가 `rp` executable을 제공한다. 따라서 installer는 기존 `~/.local/bin/rp`를 덮어쓰지 않으며, PATH의 다른 위치에서 `rp`가 발견돼도 shadowing하지 않고 충돌로 중단한다.

`help`, `help doctor`, `help room`, `help uninstall`, 모든 `<command> --help`는 문서를 열지 않고도 다음 행동을 알 수 있게 설명한다: 제품 목적과 버전, doctor, room create/join/pairing, `rp`의 session-scoped Remote Control opt-in과 opt-out, 상태, handoff/question, leave/stop/logs, 제거, 보안 주의사항, 추가 help.

### Claude Code skill

plain `/research-peer`는 overview/fallback이고, plugin action skill은 Claude의 `/` 검색 목록에서 자동완성처럼 노출된다.

```text
/research-peer:make [room-name]
/research-peer:join [invite-code]
/research-peer:ask [question]
/research-peer:handoff [experiment]
/research-peer:rooms
/research-peer:use [room]
/research-peer:status
/research-peer:peers
/research-peer:leave [room]
/research-peer:delete [room]
/research-peer:help
```

인자가 필요한 action을 인자 없이 실행하면 Claude가 필요한 값 하나를 물어본다. 특히 `make`는 room 이름, `join`은 invite, `ask`는 질문을 요청한다. 한 skill 내부의 두 번째 단어를 Claude slash menu가 완성한다고 가정하지 않고 각 action을 독립 plugin skill로 제공한다.

**[OFFICIAL]** personal skill은 `~/.claude/skills/research-peer/SKILL.md`로 `/research-peer`를 제공할 수 있다. plugin 안의 skill/tool에는 plugin scope/namespace가 붙는다. 따라서 personal skill은 얇은 stable UX이고, 별도 skills-directory plugin은 Channel/MCP server를 제공한다. peer가 보낸 inbound text는 skill 또는 uninstall을 호출할 local-owner 승인으로 간주하지 않는다.

**[SERVER-VERIFIED]** 설치된 personal skill에서 plain `/research-peer help`가 실제 Claude Code session 안에서 실행됐다. plugin root `.mcp.json`도 MCP server 한 개를 발견시켰고 `/mcp`에서 connected/2 tools로 표시됐다. 실제 두 Claude process 사이의 QUESTION/ANSWER context 왕복도 통과했다.

**[SERVER-VERIFIED]** 정상 사용자 진입점은 인자 없는 `rp`다. 이는 Channel flag와 `--remote-control`을 함께 전달해 Claude Code를 시작하고, 활성 room이 정확히 하나면 자동 binding한다. canonical command인 인자 없는 `research-peer`는 같은 Channel launcher이되 Remote Control은 기본 off다. 이후 create/join/status/ask/leave는 `/research-peer` 또는 자연어로 수행한다. **[OFFICIAL]** Channel은 session-start opt-in이므로 이미 열린 일반 Claude session에서 slash command만으로 inbound injection을 동적으로 활성화할 수는 없다.

**[OFFICIAL]** marketplace는 plugin skill/MCP/Channel의 discovery, cache install, version/update를 제공하지만 Research Peer의 별도 per-user daemon/service/CLI 설치 수단은 아니다. v1 distribution은 trusted Git/release의 `./install.sh`로 runtime을 한 번 설치하고, marketplace는 Claude component 배포에 사용한다. plugin action skills는 `/research-peer:make` 같은 namespace를 사용하고, installer가 설치하는 thin personal skill은 plain `/research-peer`를 제공한다.

### Room과 session

- `display_name`과 UUID `room_id`는 분리한다.
- 같은 표시 이름이 여러 개면 ID/고유 prefix를 요구하며 몰래 하나를 고르지 않는다.
- 중앙 discovery는 없다. 같은 이름 입력만으로 발견하지 않는다.
- creator는 만료·일회용 token이 든 invite를 만들고 기존 안전한 채널로 전달한다.
- joiner와 creator는 public-key fingerprint/pairing code를 확인한다.
- 여러 room과 session을 저장할 수 있지만 v1에서 Claude session당 활성 room은 최대 하나다.
- 없는/종료/stale session을 다른 session으로 자동 reroute하지 않는다.
- leave는 해당 local room의 session binding·수신·pending retry를 중단하고 history는 보존한다.
- delete는 local owner에게 exact dry-run plan과 명시적 confirmation을 요구한 뒤 선택한 room의 local messages/outbox/invites/membership/counters만 제거한다. project/artifact, 다른 room, remote peer data는 제거하지 않는다.

## Invite와 pairing

Invite는 URL-safe encoded, versioned JSON이며 protocol version, room UUID/display name, creator endpoint, transport, creator certificate/public-key fingerprint, expiry, one-time token을 포함할 수 있다. token은 log/help/Git에 출력하지 않으며 creator state에는 원문 대신 hash를 저장한다.

기본 흐름:

1. A가 room과 일회용 invite를 생성한다.
2. B가 invite의 fingerprint와 만료를 확인해 join한다.
3. B는 pinned TLS로 A에 접속하고 token, 자기 identity certificate/fingerprint, receive endpoint를 보낸다.
4. A는 token을 한 번만 소비하고 B를 room allowlist에 저장한다.
5. B는 A를 저장한다. 이후 양쪽 message는 paired identity 서명을 검증한다.

다른 서버 주소·계정·credential을 추측하지 않는다. 현재 server에는 실제 peer target이 없으므로 peer 연결 완료는 아직 검증되지 않았다.

## Protocol

공통 envelope 최소 필드는 다음과 같다.

```json
{
  "protocol_version": "1",
  "message_id": "UUID",
  "room_id": "UUID",
  "type": "QUESTION",
  "from": {"user": "sangyoon", "session": "followup-experiment"},
  "to": {"user": "alice", "session": "toy-baseline"},
  "request_id": "UUID",
  "created_at": "RFC3339 timestamp",
  "reply_required": true,
  "owner_attention": false,
  "body": {"text": "Reported Recall@10은 seed별 평균인가요?"}
}
```

규칙:

- 허용 type은 `HANDOFF`, `QUESTION`, `ANSWER`, `ARTIFACT_REF`, `STATUS`, `ACK`뿐이다.
- ANSWER는 QUESTION의 `request_id`를 유지한다.
- UUID/RFC3339/type/body를 schema validation한다.
- unknown type/version, 과대 payload, 너무 오래되었거나 미래인 timestamp, replay, 서명 불일치, room 비회원은 안전하게 거부한다.
- `message_id`는 receiver dedup key다. ACK 유실로 재전송돼도 side effect는 한 번이다.
- wire packet은 canonical envelope, signer fingerprint, nonce, signature를 포함한다.
- v1 payload 상한은 256 KiB, clock skew 허용은 ±5분, 보관 replay window는 최소 24시간이다.
- 한 연결의 packet은 길이 제한 JSON frame으로 전달하고 ACK는 저장 성공 후 돌려준다.

## HANDOFF schema

HANDOFF body는 다음 항목을 명시적으로 표현하고, 빈 항목도 `unknown` 또는 빈 목록으로 구분한다.

- 실험 목적, 가설
- repository identifier, Git remote, branch, commit, modified files
- data, model, checkpoint
- exact command, environment
- seeds, hyperparameters
- metrics, aggregation method/code
- raw logs와 result artifact references
- successful results, failed attempts
- confirmed facts
- Claude/researcher interpretations
- unverified assumptions
- remaining questions
- follow-up cautions

artifact는 Git commit, 접근 가능한 URL, 공유 storage path, content hash, 또는 owner가 명시적으로 허용한 파일 참조만 전달한다. transcript, 환경변수, credential, 상대 home, 파일 내용은 자동 전송하지 않는다.

## Delivery, presence, retry

- 발신 message를 먼저 SQLite outbox에 durable 기록한다.
- 즉시 전송 실패 시 `pending`으로 남기고 jitter가 있는 exponential backoff(상한 포함)를 적용한다.
- authenticated ACK 후 `delivered`로 바꾼다.
- daemon restart 뒤 pending/attempting을 복구한다.
- transient offline, retry 예정, retry exhausted/permanent failure를 status에 구분한다.
- receiver는 inbox/dedup 저장 후 session binding이 있을 때만 Channel adapter에 노출한다.
- 동일 room에서 ordering sequence를 보존하되 서로 독립인 room은 섞지 않는다.
- presence에는 user와 session alias/UUID, last_seen, active room, stale 여부가 분리된다.

## Claude Channel과 Remote Control

**[OFFICIAL]** custom Channel은 local stdio MCP server이며 `experimental['claude/channel'] = {}`를 선언하고 `notifications/claude/channel`을 보내면 열린 기존 session의 context에 `<channel ...>` provenance로 event가 들어간다. Channel notification 자체에는 Claude 처리 ACK가 없으므로 Research Peer의 transport ACK와 Claude-consumption 상태는 분리한다. custom Channel은 research preview이고 `--dangerously-load-development-channels plugin:research-peer@skills-dir`가 필요하다. **[SERVER-VERIFIED]** 이 서버의 2.1.231에서 Channel 연결 acceptance를 통과했고 현재 2.1.234에서 development Channel과 `--remote-control` 동시 flag parsing을 확인했다. 조직의 `channelsEnabled`는 이 우회보다 우선한다.

Channel은 session 시작 시 load한다. `/research-peer:use ROOM`은 local binding을 활성화하고 `/research-peer:leave`는 binding을 끊는다. daemon은 유지될 수 있지만 inactive room event는 Claude context로 들어가지 않는다. 실행 중에 development Channel을 임의 load/unload할 수 있다고 가정하지 않는다.

Research Peer Channel은 **permission relay capability를 절대 선언하지 않는다.** peer message는 user approval이 아니다. inbound에는 `room`, authenticated `sender`, `message_id`, `request_id`, `type`, `untrusted_peer_input=true` provenance를 표시한다. outbound MCP tools는 message 전송만 하며 config 변경, pairing, leave/delete, uninstall, permission 승인 도구를 노출하지 않는다.

**[OFFICIAL]** Remote Control은 `claude --remote-control` 또는 `claude remote-control`로 자기 claude.ai 계정의 local session을 모바일/브라우저에서 조작한다. local process가 살아 있어야 하며 약 10분 이상의 network outage에서 종료될 수 있다. 모바일 push는 사용자가 terminal에 focus 중이면 생략되는 등 보장되지 않는다. Research Peer는 Remote Control을 transport로 쓰지 않는다. **[SERVER-VERIFIED]** 문서화된 인자 없는 `rp` 실행 자체를 해당 local-owner session의 명시적 opt-in으로 취급하며 Claude global setting은 바꾸지 않는다. 인자 없는 `research-peer`와 `rp start --no-remote-control`은 off 경로다. Remote Control이 꺼지거나 실패해도 P2P daemon은 계속 동작한다. launcher는 `--continue`/`--resume`을 명시적으로 받은 경우에만 기존 conversation을 resume한다.

## 설치와 제거

기본 XDG layout:

```text
~/.local/bin/research-peer
~/.local/bin/rp
~/.local/share/research-peer/
~/.config/research-peer/
~/.local/state/research-peer/
~/.cache/research-peer/
~/.claude/skills/research-peer/SKILL.md
~/.claude/skills/research-peer-plugin/.claude-plugin/plugin.json
~/.config/systemd/user/research-peer.service
$XDG_RUNTIME_DIR/research-peer/
```

installer는 생성/수정 항목, category, ownership, 기존 값/backup을 install manifest에 기록하고 반복 실행에 안전해야 한다. user-scope만 사용하고 sudo나 다른 home이 필요하지 않다.

uninstall은 상태 확인 → 정확한 plan → 확인 → service stop/disable → process/socket/PID 정리 → plugin/personal skill/Research Peer가 소유한 설정 key/CLI/package 제거 → Research Peer 전용 local state 제거 → daemon reload → residue scan 순서다. project repository와 experiment artifact는 Research Peer local state로 분류하지 않으며 항상 보존한다.

- 기본: program/plugin/skill/service/runtime config와 Research Peer 전용 identity/private key, room membership, history, outbox, logs, cache를 제거한다. project repository/artifact와 unrelated 사용자 파일은 보존한다.
- `--dry-run`: mutation 없음.
- `--keep-data`: 명시적인 예외로 Research Peer rooms/history/keys/outbox/log를 보존한다.
- `--yes`: 정확한 owned target에 한해 confirmation 생략.
- `--purge`: 호환성을 위한 alias이며 기본 full local removal과 같다.

manifest에 없는 broad path, `$HOME`, `~`, repository root, glob은 삭제하지 않는다. symlink target을 따라가지 않고 unrelated Claude settings/skill/plugin, project repository/artifact, remote peer data를 보존한다. JSON 수정 시 Research Peer key만 backup+atomic write로 제거하고 소유하지 않은 Remote Control 설정은 건드리지 않는다. self-uninstall은 계획을 memory에 적재한 뒤 수행한다. 두 번째 uninstall도 성공해야 한다.

## 서비스 운영

우선순위는 `systemd --user`, tmux, screen, foreground다. crash restart, stale PID/socket cleanup, permission `0700` directory/`0600` secret, local log rotation을 지원한다. **[SERVER-VERIFIED]** 현재 서버는 user systemd가 running이고 lingering=yes이며 tmux 3.2a가 있어 user service가 SSH logout 후 유지될 조건을 충족한다. 실제 logout survivability는 destructive/session-changing test를 하지 않아 추정이다.

## v1 완료 조건

완료 판정에는 다음 22개가 모두 필요하다.

1. 격리된 서로 다른 user/process/session의 pairing
2. A room create와 B invite join
3. A HANDOFF → B
4. B adapter가 현재 context용 event로 HANDOFF 수신
5. B QUESTION 생성
6. A ANSWER 전송
7. B가 request_id로 연결
8. 실패 시 outbox
9. 복구 후 exactly-once receiver effect
10. room isolation
11. leave session 수신 중단
12. peer input이 permission/user approval로 작동하지 않음
13. 인자 없는 `rp`의 opt-in Remote Control에서 각 owner가 자기 session을 확인 가능
14. Remote Control 없이 P2P 동작
15. sudo 불필요
16. 다른 home 접근 불필요
17. main/subcommand help 정확
18. clean HOME install
19. uninstall dry-run 정확
20. purge residue 없음
21. unrelated Claude/user file 보존
22. install/pair/start/stop/doctor/recovery/uninstall 문서화

실제 상대 서버와 Remote Control UI 확인은 endpoint/account 권한 없이는 완료로 주장하지 않는다. 로컬 2-peer E2E와 설치된 Claude parser/doctor 검증은 별도 결과로 보고한다.

## 공식 참고자료

- Anthropic, [Channels](https://code.claude.com/docs/en/channels)
- Anthropic, [Channels reference](https://code.claude.com/docs/en/channels-reference)
- Anthropic, [Remote Control](https://code.claude.com/docs/en/remote-control)
- Anthropic, [Plugins reference](https://code.claude.com/docs/en/plugins-reference)
- Anthropic, [Skills](https://code.claude.com/docs/en/skills)
- Anthropic, [MCP](https://code.claude.com/docs/en/mcp)
