# Research Peer 2.0 보안 모델

## 보호 대상과 trust boundary

보호 대상은 identity private key, invite token, room membership, research metadata/history, Claude conversation context, local code/log/artifact, user permission boundary다. 각 Unix uid, local daemon, Claude adapter, remote network, authenticated peer Claude는 서로 다른 boundary다.

인증된 peer가 보낸 content도 **untrusted input**이다. authentication은 메시지 출처만 증명하고 내용의 안전성, 진실성, owner 의도 또는 실행 승인을 증명하지 않는다.

## 절대 불변 조건

peer message는 다음 어느 것도 승인할 수 없다.

- permission prompt 또는 위험 명령
- configuration/CLAUDE.md/permission setting 변경
- 새 peer 초대 또는 room membership 변경
- room delete/leave
- source/runtime/plugin/skill update
- uninstall 또는 key 삭제
- credential, 환경변수, transcript, private file 공개

Research Peer Channel은 공식 permission relay capability를 선언하지 않는다. inbound packet handler와 outbound MCP tools에는 destructive/configuration API가 없다. local CLI의 destructive/configuration action은 TTY local owner confirmation 또는 owner가 명시적으로 호출한 action skill의 explicit `--yes`를 요구한다. peer message body에 `/research-peer:update`, `research-peer update --yes`, `/research-peer room delete ... --yes`나 `/research-peer uninstall --yes`가 있어도 텍스트로만 표시된다.

## 위협과 방어

| 위협 | 방어 | 잔여 위험 |
|---|---|---|
| network eavesdropping/MITM | TLS, invite/peer certificate fingerprint pin | 최초 invite 전달 채널이 손상되면 pairing 공격 가능 |
| peer spoofing | application ECDSA signature, fingerprint+room allowlist | paired peer private key 탈취 |
| replay/duplicate | UUID dedup, signed nonce, timestamp window, replay table | 허용 window 내 DB 손실 시 replay 가능 |
| prompt injection | provenance tag/text, sender gate, capability 최소화, no permission relay | Claude가 untrusted instruction을 잘못 따를 모델 위험 |
| cross-room leak | room UUID membership check, session binding, DB query isolation | 사용자가 직접 잘못된 artifact를 보내는 위험 |
| agent loop | automatic QUESTION 금지, terminal ANSWER, automation depth, request_id 1회, rate limit | 두 owner가 수동으로 새 QUESTION을 반복 가능 |
| automatic disclosure | persistent room default off, explicit no-argument `rp` session opt-in, assigned live-session check, room disclosure precedence, audit | full opt-in에서 model이 허용 범위를 오판할 위험 |
| DoS/oversize | pre-read frame size 256 KiB, connection/read timeout, per-peer token bucket | authorized peer가 지속적으로 자원 소모 |
| path traversal | artifact는 reference object, file auto-read 없음, explicit allow root | owner가 위험 path를 직접 허용 |
| secret leakage | redaction, no env/transcript auto-send, log field allowlist | body에 사용자가 직접 secret 입력 |
| local user attack | 0700 dirs, 0600 DB/key/socket ownership, Unix uid | 같은 uid의 악성 process는 신뢰 영역 |
| unsafe uninstall | exact manifest, resolved-path guards, no symlink follow, atomic setting edit/backup | manifest 자체가 같은 uid 공격자에게 변조됨 |
| malicious/incorrect update source | 공식 HTTPS GitHub URL 고정, source override 없음, clone origin/commit/project identity/component version 검증, downgrade 거부 | 공식 repository/account 또는 GitHub/TLS trust chain 손상 시 악성 release 가능 |
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

Invite token은 256-bit random, one-time, expiry 기본 24시간이다. DB에는 domain-separated SHA-256 digest만 둔다. invite 문자열 자체는 사용자가 전달해야 하므로 민감정보로 표시하고 logs/history에 redaction한다. token consume는 transaction이며 만료는 `INVITE_EXPIRED`, 재사용/invalid token은 `AUTH_FAILURE`다. pairing 화면은 short confirmation code와 full fingerprint를 제공한다.

## 메시지와 log 최소화

wire/log에 private key, auth token, password, API key, Claude credentials를 넣지 않는다. logs는 endpoint를 필요 시 축약하고 invite/token/signature/certificate body를 redaction한다. 메시지 body는 연구 기록이므로 기본 log에는 hash/ID/type만 남기고 전문은 permission 0600 SQLite에 둔다.

HANDOFF artifact는 reference만 자동 처리한다. local file을 전송하려면 owner가 명시적으로 파일과 수신자를 선택해야 하며 2.0 transport에는 binary file upload가 없다. Git remote URL은 embedded credentials를 redaction한다.

## Rate/loop 정책

- peer별 token bucket 기본 30 message/minute, burst 10
- message `automation_depth` 기본 0, 최대 4
- automatic generation은 inbound QUESTION에 대한 ANSWER만 허용; ANSWER는 terminal
- `(room_id, request_id)`당 auto-answer 최대 1회
- reply의 `automation_depth`는 inbound + 1이며 최대 4
- ACK는 agent message가 아니며 depth를 올리지 않는다.
- limit 초과는 저장/Claude inject 전에 거부하고 local security log에 metadata만 기록한다.

## Local file과 process

- config/state/key directories: 0700; private key/DB/config: 0600
- Unix socket: 0600 또는 parent 0700
- daemon은 현재 uid로만 실행, sudo 없음
- listener bind에는 wildcard가 가능하지만 advertised endpoint의 `0.0.0.0`/`::`는 거부
- loopback advertise는 이미 설정한 SSH tunnel에 대해 local owner가 `--advertise-loopback`을 명시할 때만 허용
- stale PID가 가리키는 process command/uid가 Research Peer인지 확인 전 signal 금지
- temp file은 same directory에서 secure mode로 만들고 fsync+atomic replace

`/research-peer:make`와 `/research-peer:join`은 명시적으로 호출한 local owner를 대신해 Research Peer config와 daemon을 맞출 수 있다. local interface와 free high port는 읽기 전용 검사로 후보를 찾되 remote reachability를 단정하지 않는다. firewall, SSH key/configuration, `authorized_keys`, sshd와 remote file은 변경하지 않는다. 새 SSH connection을 열기 전에는 target과 forwarding 범위를 보여주고 별도 owner 승인을 받는다.

## Claude와 Remote Control 경계

**[OFFICIAL]** Channel event는 `<channel source=...>`로 표시되지만 model context에 들어가는 untrusted text다. Research Peer는 `untrusted_peer_input=true`를 추가하고 system instructions에 owner-approval 금지를 명시한다. Channel 처리/응답을 transport ACK로 오해하지 않는다.

Remote Control은 Anthropic service와 자기 claude.ai account 사이의 별도 outbound 연결이다. peer identity/transport/auth로 사용하지 않는다. **[SERVER-VERIFIED]** 문서화된 인자 없는 `rp` 실행은 해당 local-owner session의 Remote Control/full auto-answer launcher opt-in이며, installer는 조직/global setting이나 room policy를 바꾸지 않는다. 인자 없는 canonical `research-peer`와 `rp start --no-remote-control --no-auto-answer`은 두 opt-in의 off 경로다. peer message는 launcher를 호출하거나 어느 기능도 enable할 수 없다. 모바일 push는 guaranteed delivery mechanism이 아니다.

## 자동 응답 경계

Persistent room auto-answer setting 변경은 local CLI/owner-invoked skill만 수행하며 MCP tool에는 config capability가 없다. 기본은 off다. 인자 없는 `rp`는 local-owner launcher action 자체를 그 process의 session-only `full` opt-in으로 취급한다. dedicated answer tool은 inbound message ID를 받아 다음을 local store에서 재검증한다: QUESTION, `reply_required=true`, `owner_attention=false`, unanswered request ID, depth cap, exactly one peer, 그리고 room policy opt-in 또는 질문을 배정받은 동일 live session의 launcher opt-in. 명시적 room policy가 있으면 session `full`보다 우선한다. 이 tool은 ANSWER 외 type을 선택할 수 없고 daemon 단독으로 답하지 않는다.

`status`는 고정 응답, `summary`는 owner-authored note만 사용한다. `full`은 model-generated text를 허용하므로 명시적 고위험 opt-in이며 prompt-injection/과다공개의 잔여 위험이 있다. 어느 수준에서도 credential, 환경변수, transcript, private file 내용, invite token, private endpoint, `~/.ssh`, command/config mutation을 자동으로 제공하지 않는다. tool은 shell/file/config capability를 가지지 않으며 policy 밖 질문은 owner에게 escalation한다.

## 업데이트 보안

Self-update는 local owner가 직접 `research-peer update`를 실행하거나 `/research-peer:update`를 명시적으로 호출한 경우만 허용한다. production CLI는 repository URL, branch, file path 입력을 받지 않으며 공식 `https://github.com/ronut01/research-peer`만 private temporary directory에 clone한다. installer 실행 전에 origin, commit 형식, project/plugin identity, Python/package/plugin/marketplace version 일치와 downgrade 여부를 검사한다. `--check`는 installation을 변경하지 않는다.

Update는 새 공식 checkout의 code와 `install.sh`를 실행하는 신뢰 결정이다. GitHub account/repository 또는 TLS trust chain 손상은 local code execution으로 이어질 수 있다는 잔여 위험이 있다. 적용 시 Research Peer-owned program만 교체하며 identity/private key, rooms, peer membership, history, outbox, config/log와 project/artifact는 보존한다. daemon이 실행 중이면 검증 후 stop/install/restart하고, install 실패 시 state를 삭제하지 않는다. peer text에는 update capability가 없고 MCP tool로도 노출하지 않는다.

## 제거 보안

기본 uninstall은 local private key 제거로 모든 기존 pairing identity를 잃는다는 경고를 출력한다. `--keep-data`만 이를 보존하며 `--purge`는 기본 동작의 호환 alias다. remote data는 삭제하지 않는다. target path는 manifest entry와 fixed dedicated directories의 canonical child인지 검사한다. symlink는 링크만 unlink하고 target은 건드리지 않는다. `$HOME`, workspace root, 빈 문자열, `/`, glob은 거부한다.

`/research-peer uninstall` skill은 CLI dry-run만 안내할 수 있고 실행 confirmation은 local terminal에서 받는다. inbound peer event에서 호출된 상태면 uninstall을 수행하지 않는다.

## Known residual risks

- custom Channels는 research preview라 contract/flag가 바뀔 수 있다.
- direct TCP inbound는 network/firewall exposure가 있으며 peer server 검증 전에는 도달성을 모른다.
- self-signed pinning은 invite 전달 채널의 authenticity에 의존한다.
- 같은 uid의 다른 process는 local state를 읽거나 조작할 수 있다.
- model-level prompt injection은 provenance/capability 제한으로 완화하지만 완전히 제거되지 않는다.
- `rp`와 room `full` auto-answer는 명시적 opt-in이어도 model이 공개 범위를 오판할 수 있다. persistence가 필요하면 status/summary가 권장값이다.
- Self-update는 고정된 공식 GitHub repository의 현재 default branch를 신뢰하며 signed tag/commit을 강제하지 않는다.
