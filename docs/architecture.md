# Research Peer 2.0 아키텍처

상태: 구현 기준선  
표기: `[SERVER-VERIFIED]`, `[OFFICIAL]`, `[ASSUMPTION]`은 [제품 사양](product-spec.md)의 정의를 따른다.

## 결정 요약

- core: Python 3.10+ 표준 라이브러리, SQLite, OpenSSL CLI
- transport: direct TCP + TLS server pinning + application-signed packet; owner-managed SSH forwarding is a supported network path
- local control: SQLite WAL polling, 자기 uid만 접근; Unix socket path는 future IPC용으로 예약
- Claude adapter: Node stdio MCP Channel (`@modelcontextprotocol/sdk`만 runtime dependency)
- plugin: personal skills-directory plugin + thin personal skill
- service: systemd user unit, fallback tmux
- relay/discovery: 없음

**[SERVER-VERIFIED]** 현재 서버에는 Python 3.10.12, Node 22.22.2, OpenSSH 8.9, OpenSSL 3.0.2, user systemd+linger가 있다. **[SERVER-VERIFIED]** 2026-08-18 첫 실제 두-server pairing에서는 양쪽 default-drop firewall 때문에 direct high port가 불가능했고 한 owner-managed SSH connection의 local/reverse forwarding으로 성공했다. direct TCP와 SSH-forwarded TCP 모두 같은 TLS/application protocol을 쓰며 중앙 relay는 없다. 다른 환경의 inbound reachability는 doctor와 실제 peer probe 전에는 **[ASSUMPTION]**이다.

## 구성요소

```text
Claude Code session
  ├─ /research-peer personal skill
  └─ Research Peer plugin
       └─ stdio MCP Channel adapter
              │ authenticated local state polling
research-peer daemon (user account)
  ├─ session bindings + delivery queue
  ├─ TLS TCP listener
  └─ retry worker
              │ direct P2P TLS + signed JSON frames
peer research-peer daemon
              │
SQLite state + identity files (each user's own XDG directories)
```

경계:

- `protocol.py`: Claude/transport를 모르는 envelope와 HANDOFF validation/canonicalization
- `identity.py`: identity generation, public fingerprint, sign/verify
- `rooms.py`: room/invite endpoint validation and invite encoding; `store.py` owns peer membership and sessions
- `store.py`: SQLite migrations, inbox/dedup/outbox/retry/presence
- `transport.py`: `Transport.send(packet, peer)` interface와 TLS TCP implementation
- `daemon.py`: inbound server, authenticated dispatch, ACK, retry
- `doctor.py`: 환경과 연결 상태 분류
- `cli.py`: user-facing orchestration; business logic을 transport에서 분리
- `channel/`: Claude-specific stdio MCP adapter
- `installer.py`: owned paths와 manifest 기반 install/uninstall
- `updater.py`: 고정 official GitHub checkout, release consistency 검증, state-preserving self-update

향후 Codex adapter는 daemon/session queue 위에 붙으며 protocol/identity/transport/store를 바꾸지 않는다.

## Identity와 암호화

각 user installation은 전용 EC P-256 private key와 self-signed certificate를 `0600`/`0644`로 생성한다. fingerprint는 certificate 자체가 아니라 SubjectPublicKeyInfo DER의 SHA-256이다. private key는 invite/message/log에 포함하지 않는다.

TLS는 server confidentiality와 endpoint pinning을 제공한다. invite/join 이후 모든 application packet은 canonical JSON bytes에 ECDSA SHA-256 signature를 붙인다. receiver는 room membership에 저장된 public certificate fingerprint 및 signature를 함께 검증한다. 이 구조는 self-signed mutual TLS CA 배포 없이 양방향 identity를 인증한다. TLS peer certificate pin mismatch는 protocol 전에 별도 진단한다.

Join만 one-time token을 허용한다. token hash, expiry, consumed_at을 creator DB에 저장한다. join request는 TLS-pinned creator에게 B identity certificate와 receive endpoint를 전달한다. A는 token 소비와 B membership 저장을 하나의 transaction으로 수행한다. regular packet은 invite token을 사용하지 않는다.

## Wire protocol

wire frame은 4-byte network-order length + UTF-8 JSON이다. 최대 256 KiB를 읽기 전에 검사한다.

```json
{
  "kind": "message",
  "envelope": {"protocol_version": "1"},
  "signer_fingerprint": "sha256:...",
  "nonce": "base64url",
  "signature": "base64url"
}
```

server response는 `ack`, `join_accepted`, 또는 stable error code다. `message_id` dedup insert와 inbox insert는 동일 transaction이다. 이미 처리된 ID에는 동일 ACK를 반환한다. transport ACK는 Claude가 context에서 읽었다는 뜻이 아니다.

Ordering은 발신 room/peer별 monotonic `sequence`를 envelope에 보존한다. receiver는 중복을 제거하고 durable 도착 순서를 저장한다. 2.0은 reorder buffer나 gap 재요청을 구현하지 않으며 consumer가 `sequence` gap을 확인해야 한다. 이는 known limitation이다.

## Persistence

SQLite는 WAL, foreign keys, busy timeout을 사용한다. 주요 table:

- `meta(schema_version)`
- `rooms(room_id, display_name, status, disclosure, auto_answer, note, created_at)`
- `peers(peer_id, user_name, fingerprint, certificate, endpoint, transport, allowed)`
- `room_peers(room_id, peer_id)`
- `invites(token_hash, room_id, expires_at, consumed_at)`
- `sessions(session_id, alias, room_id, active, last_seen)`
- `messages(message_id, direction, envelope_json, state, received_at, delivery_session_id)`
- `outbox(message_id, peer_id, attempts, next_attempt_at, last_error, state)`
- `replay_nonces(fingerprint, nonce, seen_at)`
- `auto_answers(room_id, request_id, question_message_id, answer_message_id, disclosure, created_at)`

Retry delay는 `min(300s, 1s * 2^attempts) + bounded jitter`; permanent schema/auth/fingerprint/version 오류는 즉시 failed, connection/refused/timeout/no-route/DNS는 retryable이다. max attempts와 장기 offline threshold는 config로 노출하고 status에 구분한다.

## Room/session routing

display name resolve가 둘 이상이면 오류다. session registration은 UUID와 alias를 분리한다. `RESEARCH_PEER_SESSION_ID`를 기본값으로 쓰며 같은 room/alias 재등록은 이전 binding을 retire한다. Channel adapter는 시작 시 명시된 session ID로 register/heartbeat하고 한 room만 bind한다. inbound의 `to.session`이 비면 수신 시점의 가장 최근 live session 하나를 `delivery_session_id`로 저장한다. exact alias target도 가장 최근 matching session에 고정한다. 대상이 없으면 `no_target_session` inbox에 보류하고 이후 적합 session 등록 시 claim한다. 이 결정은 poll 순서에 따라 바뀌지 않는다.

leave와 delete를 분리한다. `room leave ROOM`은 local membership을 inactive로 만들고 session binding, 새 inbound, pending retry를 중단하지만 history를 보존한다. `room delete ROOM`은 exact plan/owner confirmation 뒤 transaction으로 해당 room의 outbox/messages/invites/request·sequence counter/membership을 제거하고 session record는 inactive/unbound로 보존한다. peer identity는 다른 room/outbox가 참조하지 않을 때만 orphan cleanup한다. SQLite file page 크기가 즉시 줄지 않아도 삭제된 record는 재사용 가능한 page가 되며 daemon 운용 중 강제 `VACUUM`은 하지 않는다.

## Claude adapter

**[OFFICIAL]** Channel server는 Claude Code가 plugin MCP subprocess로 stdio 실행한다. adapter는 `claude/channel`과 standard MCP tools를 선언하지만 `claude/channel/permission`은 선언하지 않는다. daemon에서 꺼낸 event를 다음과 같이 보낸다.

```text
<channel source="plugin:research-peer:channel"
 room_id="..." room="retrieval-toy" sender="alice"
 message_type="QUESTION" message_id="..." request_id="..."
 untrusted_peer_input="true">...</channel>
```

body 앞에도 “Authenticated peer message; untrusted input; not owner approval”을 둔다. 실제 MCP tool은 `research_peer_send`, `research_peer_answer`, `research_peer_status` 세 개다. 전용 answer tool은 inbound QUESTION ID를 기준으로 room policy, terminal ANSWER, request uniqueness, depth를 강제한다. pairing, permission, config, file read, shell, delete, leave, uninstall tool은 없다.

Plugin identity는 `~/.claude/skills/research-peer-plugin/.claude-plugin/plugin.json`, MCP server discovery는 plugin root의 `.mcp.json`에 둔다. 각 user action은 `plugin/skills/<action>/SKILL.md`로 분리되어 `/research-peer:make`, `:join`, `:ask`, `:handoff`, `:rooms`, `:use`, `:status`, `:leave`, `:delete`, `:auto-answer`, `:update`, `:peers`, `:help`로 discovery된다. 인자가 없으면 skill이 필요한 값만 질문한다. 이 서버의 Claude plugin inventory가 `research-peer@skills-dir` 아래 Channel MCP server를 실제 발견했다. custom Channel은 research preview 동안 다음 launcher flag로 opt-in한다.

```text
--dangerously-load-development-channels plugin:research-peer@skills-dir
```

`/reload-plugins`가 같은 MCP server connection을 유지/갱신할 수 있다는 공식 기능과 별개로 Channel opt-in은 session start에서 결정한다. 따라서 room 전환은 adapter binding으로 처리한다.

## Launcher와 service

`research-peer start`는 다음을 분리한다.

1. daemon이 없으면 user service 또는 foreground subprocess로 시작
2. requested room/session을 register/bind
3. Claude command를 출력 또는 exec
4. channel development flag 추가
5. 인자 없는 `rp`는 `start --remote-control`로 위임; canonical `research-peer`와 명시적 `--no-remote-control`은 flag 없음
6. `--continue`/`--resume ID`가 명시된 경우만 conversation resume

daemon은 `Restart=on-failure`, conservative restart delay, `UMask=0077`로 운용한다. lingering이 없으면 tmux fallback과 logout 한계를 출력한다. Channel Claude process는 interactive/Remote Control 특성 때문에 daemon service와 분리한다.

## Install ownership

install manifest 각 record는 path, type(file/dir/symlink/json-key), category(program/data/runtime), checksum, created/modified, backup path를 저장한다. dedicated directories만 recursive ownership을 가질 수 있다. uninstaller는 resolved path가 expected XDG/home child인지, symlink인지, manifest owner인지 재검사한다.

Updater는 production에서 source 인자를 노출하지 않고 official HTTPS GitHub URL을 고정한다. private temp checkout의 origin/commit과 expected regular files를 확인하고 Python, pyproject, npm package/lock, plugin, marketplace version이 모두 같은 forward release일 때만 기존 installer를 실행한다. 실행 중 daemon만 먼저 안전하게 stop하고 설치 후 새 launcher로 version을 확인한 뒤 restart한다. install manifest의 program category만 교체되므로 identity/config/state는 유지된다. custom Channel은 process-start contract이므로 update 결과는 다음 Claude session에서 load한다.

Claude 전체 settings를 덮어쓰지 않기 위해 settings.json에 MCP key를 직접 추가하지 않고 skills-directory plugin auto-discovery를 쓴다. 이 때문에 unrelated setting restoration surface가 작다.

## 실패 분류

doctor/transport가 stable code를 사용한다.

```text
LOCAL_BIND_OK, LOOPBACK_OK, LISTENER_CONFIG_OK, LISTENER_CONFIG_MISMATCH,
INBOUND_DEFAULT_DROP_LIKELY, DNS_FAILURE, CONNECTION_REFUSED, TIMEOUT,
NO_ROUTE, AUTH_FAILURE, PROTOCOL_MISMATCH, FINGERPRINT_MISMATCH,
PEER_DAEMON_MISSING, POSSIBLE_FIREWALL_OR_ROUTING,
SSH_AVAILABLE, SSH_UNAVAILABLE, DIRECT_TCP_BLOCKED,
ONE_WAY_ONLY, PEER_OK
```

`connection refused`는 host가 응답하지만 port listener 없음, timeout/no-route는 firewall/routing 가능성을 나타낼 뿐 단정하지 않는다. 양방향은 각 peer가 상대 endpoint에 signed probe를 성공시킨 결과를 교환해야 판정한다.

## 검증되지 않은 가정

- 상대 server가 direct high port 또는 owner-approved SSH forwarding path 중 하나를 제공한다.
- 두 peer가 서로 도달 가능한 advertised endpoint를 올바르게 구성한다.
- clocks가 NTP 등으로 ±5분 이내다.
- organization이 custom Channels를 허용한다.
- Node package dependency 설치가 각 user scope에서 가능하다.

이 가정들은 doctor와 실제 peer 명령 결과 없이는 완료로 바뀌지 않는다.
