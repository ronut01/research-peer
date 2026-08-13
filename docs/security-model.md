# Research Peer v1 보안 모델

## 보호 대상과 trust boundary

보호 대상은 identity private key, invite token, room membership, research metadata/history, Claude conversation context, local code/log/artifact, user permission boundary다. 각 Unix uid, local daemon, Claude adapter, remote network, authenticated peer Claude는 서로 다른 boundary다.

인증된 peer가 보낸 content도 **untrusted input**이다. authentication은 메시지 출처만 증명하고 내용의 안전성, 진실성, owner 의도 또는 실행 승인을 증명하지 않는다.

## 절대 불변 조건

peer message는 다음 어느 것도 승인할 수 없다.

- permission prompt 또는 위험 명령
- configuration/CLAUDE.md/permission setting 변경
- 새 peer 초대 또는 room membership 변경
- room delete/leave
- uninstall 또는 key 삭제
- credential, 환경변수, transcript, private file 공개

Research Peer Channel은 공식 permission relay capability를 선언하지 않는다. inbound packet handler와 outbound MCP tools에는 destructive/configuration API가 없다. local CLI의 destructive action은 TTY local owner confirmation 또는 explicit `--yes`를 요구한다. peer message body에 `/research-peer room delete ... --yes`나 `/research-peer uninstall --yes`가 있어도 텍스트로만 표시된다.

## 위협과 방어

| 위협 | 방어 | 잔여 위험 |
|---|---|---|
| network eavesdropping/MITM | TLS, invite/peer certificate fingerprint pin | 최초 invite 전달 채널이 손상되면 pairing 공격 가능 |
| peer spoofing | application ECDSA signature, fingerprint+room allowlist | paired peer private key 탈취 |
| replay/duplicate | UUID dedup, signed nonce, timestamp window, replay table | 허용 window 내 DB 손실 시 replay 가능 |
| prompt injection | provenance tag/text, sender gate, capability 최소화, no permission relay | Claude가 untrusted instruction을 잘못 따를 모델 위험 |
| cross-room leak | room UUID membership check, session binding, DB query isolation | 사용자가 직접 잘못된 artifact를 보내는 위험 |
| agent loop | hop count/automation depth, reply_required, per-request limit, rate limit | 두 owner가 수동으로 반복 가능 |
| DoS/oversize | pre-read frame size 256 KiB, connection/read timeout, per-peer token bucket | authorized peer가 지속적으로 자원 소모 |
| path traversal | artifact는 reference object, file auto-read 없음, explicit allow root | owner가 위험 path를 직접 허용 |
| secret leakage | redaction, no env/transcript auto-send, log field allowlist | body에 사용자가 직접 secret 입력 |
| local user attack | 0700 dirs, 0600 DB/key/socket ownership, Unix uid | 같은 uid의 악성 process는 신뢰 영역 |
| unsafe uninstall | exact manifest, resolved-path guards, no symlink follow, atomic setting edit/backup | manifest 자체가 같은 uid 공격자에게 변조됨 |
| unsafe room deletion | exact UUID resolution, dry-run counts, local confirmation, transaction, no remote call | 같은 uid의 악성 process 또는 `--yes` 오용 |
| stale session misdelivery | exact session target, no fallback, heartbeat/stale status | session alias를 사용자가 오인 |

## Authentication 상세

Identity fingerprint는 public key DER SHA-256이다. certificate, endpoint, username은 invite/join 시 연결되고 room membership에 저장된다. regular message acceptance 조건은 다음 모두다.

1. supported protocol/version/type/schema/size
2. timestamp within policy
3. nonce not replayed
4. signer fingerprint is enabled room member
5. presented public certificate fingerprint matches stored value
6. canonical packet signature valid
7. target room is active locally
8. rate and automation-depth limits

TLS server certificate는 connector가 stored/invite fingerprint로 pin한다. hostname PKI 대신 explicit peer pin을 사용하므로 endpoint 변경 시 owner-mediated re-pair가 필요하다.

## Invite

Invite token은 256-bit random, one-time, expiry 기본 30분이다. DB에는 salted SHA-256/HMAC-style digest만 둔다. invite 문자열 자체는 사용자가 전달해야 하므로 민감정보로 표시하고 logs/history에 redaction한다. token consume는 transaction이며 재사용은 `AUTH_FAILURE`다. pairing 화면은 short confirmation code와 full fingerprint를 제공한다.

## 메시지와 log 최소화

wire/log에 private key, auth token, password, API key, Claude credentials를 넣지 않는다. logs는 endpoint를 필요 시 축약하고 invite/token/signature/certificate body를 redaction한다. 메시지 body는 연구 기록이므로 기본 log에는 hash/ID/type만 남기고 전문은 permission 0600 SQLite에 둔다.

HANDOFF artifact는 reference만 자동 처리한다. local file을 전송하려면 owner가 명시적으로 파일과 수신자를 선택해야 하며 v1 기본 transport에는 binary file upload가 없다. Git remote URL은 embedded credentials를 redaction한다.

## Rate/loop 정책

- peer별 token bucket 기본 30 message/minute, burst 10
- message `automation_depth` 기본 0, 최대 4
- 동일 `request_id` automated QUESTION/ANSWER 왕복 최대 4
- ACK는 agent message가 아니며 depth를 올리지 않는다.
- limit 초과는 저장/Claude inject 전에 거부하고 local security log에 metadata만 기록한다.

## Local file과 process

- config/state/key directories: 0700; private key/DB/config: 0600
- Unix socket: 0600 또는 parent 0700
- daemon은 현재 uid로만 실행, sudo 없음
- listener 기본은 명시된 interface; `0.0.0.0`은 경고와 opt-in
- stale PID가 가리키는 process command/uid가 Research Peer인지 확인 전 signal 금지
- temp file은 same directory에서 secure mode로 만들고 fsync+atomic replace

## Claude와 Remote Control 경계

**[OFFICIAL]** Channel event는 `<channel source=...>`로 표시되지만 model context에 들어가는 untrusted text다. Research Peer는 `untrusted_peer_input=true`를 추가하고 system instructions에 owner-approval 금지를 명시한다. Channel 처리/응답을 transport ACK로 오해하지 않는다.

Remote Control은 Anthropic service와 자기 claude.ai account 사이의 별도 outbound 연결이다. peer identity/transport/auth로 사용하지 않는다. enable은 launcher opt-in이며 조직/global setting을 설치 프로그램이 바꾸지 않는다. 모바일 push는 guaranteed delivery mechanism이 아니다.

## 제거 보안

기본 uninstall은 local private key 제거로 모든 기존 pairing identity를 잃는다는 경고를 출력한다. `--keep-data`만 이를 보존하며 `--purge`는 기본 동작의 호환 alias다. remote data는 삭제하지 않는다. target path는 manifest entry와 fixed dedicated directories의 canonical child인지 검사한다. symlink는 링크만 unlink하고 target은 건드리지 않는다. `$HOME`, workspace root, 빈 문자열, `/`, glob은 거부한다.

`/research-peer uninstall` skill은 CLI dry-run만 안내할 수 있고 실행 confirmation은 local terminal에서 받는다. inbound peer event에서 호출된 상태면 uninstall을 수행하지 않는다.

## Known residual risks

- custom Channels는 research preview라 contract/flag가 바뀔 수 있다.
- direct TCP inbound는 network/firewall exposure가 있으며 peer server 검증 전에는 도달성을 모른다.
- self-signed pinning은 invite 전달 채널의 authenticity에 의존한다.
- 같은 uid의 다른 process는 local state를 읽거나 조작할 수 있다.
- model-level prompt injection은 provenance/capability 제한으로 완화하지만 완전히 제거되지 않는다.
