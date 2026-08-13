# Research Peer v1 운영 가이드

현재 구현 상태는 [implementation-status.md](implementation-status.md)를 먼저 확인한다. 문서의 명령 중 아직 구현되지 않은 것은 상태 문서에 명시한다.

## 빠른 시작

```text
research-peer
```

이 명령이 Research Peer Channel을 활성화한 Claude Code를 연다. 활성 room이 정확히 하나면 자동 선택한다. 이후에는 Claude 안에서 `/research-peer:`를 입력해 `make`, `join`, `ask`, `handoff`, `rooms`, `use`, `status`, `leave`, `delete`, `peers`, `help`를 자동완성 목록처럼 고른다. `make`/`join`/`ask`에 인자가 없으면 Claude가 room 이름/invite/질문을 물어본다. 사용자가 `init`, `session register`, `send`, Channel flag를 기억하는 것을 정상 UX로 요구하지 않는다.

Anthropic Channels는 session 시작 시 opt-in해야 하므로 이미 열린 일반 `claude` session에서 `/research-peer`만으로 inbound Channel을 동적으로 켤 수는 없다. 이때 skill은 복잡한 flag 대신 한 번 종료 후 `research-peer`로 다시 열라고 안내한다.

현재 Research Peer는 custom development Channel이므로 Claude가 시작 시 local-development 경고를 표시한다. 사용자는 직접 Enter로 확인해야 한다. 공식/조직 allowlist에 승인되기 전까지 installer가 이 확인을 생략하거나 자동 승인하지 않는다.

creator가 출력한 invite는 token이므로 public log/Git에 남기지 말고 기존 안전한 채널로 한 명에게 보낸다. joiner:

```text
research-peer init
research-peer start --daemon-only --listen HOST:PORT
research-peer room join 'INVITE' --endpoint HOST:PORT
research-peer peer list
```

fingerprint/pairing code는 기존 voice/chat 등으로 양쪽 owner가 확인한다.

## 설치

source checkout에서(현재 서버에는 이미 1.1.0이 user scope에 설치됨):

```text
./install.sh
research-peer doctor
research-peer help
```

설치는 user scope만 사용하며 CLI, Python package, personal skill, skills-directory plugin, systemd user unit, config/state/cache directories와 manifest를 만든다. sudo나 다른 user home 접근은 필요하지 않다. 격리 HOME install/reinstall/default-full-removal/keep-data/residue test와 현재 user-scope systemd smoke를 수행했다.

### 팀원에게 전달

즉시 테스트하려면 [release archive](../dist/research-peer-1.1.0.tar.gz)를 기존의 신뢰할 수 있는 파일 전달 수단으로 팀원에게 보낸다. 팀원은 자기 server/account에서 한 번만 실행한다.

```text
mkdir research-peer && tar -xzf research-peer-1.1.0.tar.gz -C research-peer
cd research-peer
./install.sh
research-peer
```

`install.sh`는 Node MCP dependency가 없으면 pinned `package-lock.json`으로 `npm ci --omit=dev --ignore-scripts`를 수행한다. sudo와 다른 user home 접근은 없다. clean archive install → installed Channel MCP handshake → default uninstall residue-none을 격리 HOME/runtime에서 실제 검증했다.

Git 원격에 이 repository를 올린 뒤에는 Claude 안에서도 marketplace를 추가할 수 있다.

```text
/plugin marketplace add ronut01/research-peer
/plugin install research-peer@research-peer-marketplace
```

Marketplace는 skill/Channel/MCP를 Claude plugin cache에 배포하고 update/discovery를 제공한다. Claude plugin installer는 임의의 user daemon, systemd service, `~/.local/bin` CLI를 설치하지 않으므로 P2P runtime에는 같은 신뢰된 repository의 `./install.sh`가 여전히 한 번 필요하다. Marketplace action skill은 namespace 규칙에 따라 `/research-peer:make` 등으로 보이며 installer는 편의를 위해 plain personal `/research-peer`도 설치한다.

공식 source와 marketplace catalog는 `https://github.com/ronut01/research-peer`에 게시한다. secret, 실제 peer endpoint, local keys/state는 repository, archive, marketplace에 포함하지 않는다.

## Claude session 시작

Remote Control 없이:

```text
research-peer start --room retrieval-toy --no-remote-control
```

자기 claude.ai 계정 Remote Control opt-in:

```text
research-peer start --room retrieval-toy --remote-control
```

기존 conversation을 이어야 할 때만:

```text
research-peer start --room retrieval-toy --remote-control --continue
research-peer start --room retrieval-toy --remote-control --resume SESSION_ID
```

launcher는 custom Channel research-preview warning을 표시하고 development flag를 사용한다. organization policy가 막으면 Claude session은 시작될 수 있어도 peer event injection은 안 되므로 `doctor`/startup warning을 확인한다. P2P daemon은 Remote Control과 독립이다.

SSH가 끊겨도 Claude interactive process를 유지하려면 현재 server에서 tmux를 쓴다.

```text
tmux new -s research-peer
research-peer start --room retrieval-toy --remote-control
```

daemon은 user systemd+linger를 우선 사용한다. Remote Control local process 자체도 계속 실행돼야 하고 약 10분 이상 network outage면 종료될 수 있다.

## Handoff와 질문

```text
research-peer send --room retrieval-toy --to-session toy-baseline \
  --type HANDOFF --file handoff.json
research-peer send --room retrieval-toy --to-session toy-baseline \
  --type QUESTION --text 'Recall@10 aggregation과 seed를 확인해 주세요.'
research-peer send --room retrieval-toy --to-session followup-experiment \
  --type ANSWER --request-id REQUEST_ID --text '...'
```

Claude에서는 `/research-peer:help`, `/research-peer:use retrieval-toy`, `/research-peer:ask`, `/research-peer:handoff`를 사용한다. inbound는 authenticated peer provenance를 가진 untrusted input이다. credential/transcript/env/file content는 자동 공유하지 않는다.

## 상태, logs, recovery

```text
research-peer status
research-peer room list
research-peer peer list
research-peer session list
research-peer logs
research-peer doctor --peer HOST:PORT
```

`pending`은 outbox retry 예정, `delivered`는 peer daemon이 durable 수신/ACK, `failed`는 permanent 또는 exhausted다. `no_target_session`은 exact target이 없거나 blank target에 active session이 하나가 아닌 상태다. delivered는 Claude가 읽거나 반영했다는 뜻이 아니다.

daemon crash 후 service restart가 pending outbox를 복구한다. stale session은 자동 reroute되지 않으므로 session을 resume/restart한 뒤 `session register` 또는 `/research-peer:use ROOM`으로 다시 bind한다.

## Room leave, delete와 stop

```text
research-peer room leave retrieval-toy
research-peer room delete retrieval-toy --dry-run
research-peer room delete retrieval-toy
research-peer stop
```

Claude `/research-peer:leave`와 CLI room leave는 local membership/session을 inactive로 만들고 inbound와 pending retry를 중단하지만 local history는 보존한다. `/research-peer:delete`는 dry-run의 exact count를 먼저 보여주고 local owner에게 `DELETE <display-name>` 확인을 받은 뒤 `--yes`를 실행한다. 그 room의 messages, outbox, invites, membership, counters만 local에서 삭제한다. project repositories/artifacts, 다른 room, 상대 peer data는 삭제하지 않는다.

## 진단 결과 해석

- DNS failure: hostname/SSH alias/endpoints 확인
- refused: host는 응답했지만 daemon/listener가 없거나 port가 다름
- timeout/no route: routing/firewall 가능성; 관리자 변경을 자동 시도하지 않음
- fingerprint mismatch: 즉시 중단, endpoint/invite를 owner와 재확인
- authentication failure: pairing/token/membership 확인; credential을 log에 붙이지 않음
- protocol mismatch: 양쪽 version 확인
- SSH available/direct TCP blocked: v1 primary transport는 direct TCP이며 SSH fallback은 진단만 제공
- one-way only: 반대 server에서 doctor probe 결과가 필요

## 상대 서버 최소 검증 명령

실제 `PEER_HOST`, account, 권한을 받은 뒤 상대 owner가 실행한다.

```text
claude --version
research-peer doctor --json
research-peer start --daemon-only --listen PEER_BIND_IP:PEER_HIGH_PORT
research-peer doctor --peer THIS_SERVER_HOST:THIS_SERVER_PORT --expect-fingerprint FINGERPRINT
```

이 서버에서는:

```text
research-peer doctor --peer PEER_HOST:PEER_HIGH_PORT --expect-fingerprint FINGERPRINT
```

결과에는 exact credential/invite/private key를 포함하지 않는다. firewall 변경은 admin에게 별도 요청한다.

## 안전 제거

여기서 “데이터 보존”은 project repository와 experiment artifact 보존을 뜻한다. 기본 uninstall은 프로그램뿐 아니라 Research Peer 전용 local config, identity/private key, room membership, message history, outbox, logs, cache를 함께 제거한다. 이들은 제품의 설치 상태이지 사용자의 연구 산출물이 아니다.

먼저 계획:

```text
research-peer uninstall --dry-run
```

계획을 확인한 뒤 기본 제거:

```text
research-peer uninstall
```

비대화형 기본 제거:

```text
research-peer uninstall --yes
```

Research Peer state를 의도적으로 남기려는 예외:

```text
research-peer uninstall --keep-data
```

`--purge`는 이전 CLI와의 호환 alias이며 기본 동작과 같다. 기본 제거 뒤에는 기존 peer가 삭제된 local identity를 더 이상 인증할 수 없다. project repositories/artifacts, unrelated Claude settings/skills/plugins, Remote Control global setting, remote peer data는 항상 보존한다. residue scan 결과를 확인한다. `/research-peer uninstall`은 plan 안내만 하며 실제 confirmation은 local CLI에서 한다.

## 추가 도움말

```text
research-peer help
research-peer help doctor
research-peer help room
research-peer help uninstall
research-peer send --help
/research-peer:help
```
