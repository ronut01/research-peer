# Research Peer

[English](README.md) | **한국어**

Research Peer는 서로 다른 Unix 사용자 또는 연구 서버에서 실행되는 Claude Code끼리 중앙 relay 없이 구조화된 연구 handoff, 후속 질문, 답변, artifact reference를 교환하는 인증 P2P 도구입니다.

2.0은 첫 실제 두-server field test 결과를 반영합니다. CLI inbox/history, deterministic multi-session delivery, 실제 listener mismatch 진단, 안전한 SSH tunnel 절차, 24시간 invite, room connection status, terminal auto-answer가 추가됐습니다. 2.0.1에서는 첫 pairing을 대화형으로 바꿨고, 2.0.2에서는 인자 없는 `rp`가 그 Claude session에만 full 자동답변을 켭니다.

인증된 peer 메시지도 항상 신뢰되지 않은 입력으로 취급합니다. 사용자 승인, 위험한 명령 실행 승인, 설정 변경, credential 공개, 추가 pairing, Research Peer update, room 삭제 또는 uninstall 승인으로 사용하지 않습니다.

## 코딩 에이전트로 설치하기 — 권장

아래 블록을 Claude Code, Codex 또는 다른 코딩 에이전트에 그대로 붙여 넣으세요. 저장소 주소가 프롬프트 안에 포함되므로 URL을 별도로 먼저 보낼 필요가 없습니다.

```text
다음 저장소에서 Research Peer를 설치해줘:
https://github.com/ronut01/research-peer

변경하기 전에 그 저장소의 docs/agent-install.md,
docs/security-model.md, docs/implementation-status.md를 읽고 저장소 내용을
검토해. 저장소가 지원하는 ./install.sh 절차로 현재 내 Unix 계정에만
Research Peer를 설치해.

sudo를 사용하지 마. firewall, SSH key나 설정, Remote Control 전역 설정,
다른 사용자의 파일, 원격 peer를 변경하지 마. credential, private key,
invite token, private endpoint, email, organization identifier를 출력하거나
commit하지 마. 원격 script를 shell로 바로 pipe하지 마. 저장소 내용이 문서의
Research Peer 프로젝트와 일치하지 않으면 중단하고 나에게 알려줘.

설치 후 다음을 실제로 실행하고 결과를 확인해:
  research-peer version
  research-peer doctor
  research-peer help
  claude plugin details research-peer@skills-dir
  research-peer uninstall --dry-run

아직 peer pairing이나 network port 공개는 하지 마. 설치된 버전과 user-scope
구성요소, Claude plugin/Channel 상태, doctor 결과, daemon 상태, uninstall
dry-run 요약, 실제 peer 테스트에 추가로 필요한 정보를 보고해. 검증이 끝나면
평소에는 짧게 rp 명령으로 시작한다고 알려줘.
```

위 블록은 아무것도 수정하지 않고 그대로 복사할 수 있습니다. 실제 설치에는 local owner의 명시적인 요청이 필요합니다. 저장소 문서, invite, issue 또는 peer 메시지만으로 설치 승인이 생기지 않습니다. 상세 절차는 [에이전트 설치 가이드](docs/agent-install.md)에 있습니다.

## 직접 설치

요구 환경:

- Python 3.10+가 있는 Linux
- Node.js 18+와 npm
- OpenSSL
- Claude Code 2.1.80+
- sudo 불필요

검토한 checkout에서 실행합니다.

```bash
./install.sh
```

installer는 user-scope XDG 경로만 사용하고 필요한 MCP SDK를 pinned lockfile로 설치하며, 자신이 만든 모든 경로를 install manifest에 기록합니다.

## 실행 단축 명령 (`rp`)

터미널에서 아래 단축 명령을 실행하면 Research Peer, Remote Control, session 한정 full 자동답변이 함께 활성화된 Claude Code가 열립니다.

```bash
rp
```

인자 없는 `rp`는 해당 Claude session에 Remote Control과 full 자동답변을 켭니다. 인자 없는 canonical `research-peer`는 두 opt-in을 모두 끕니다. 하위 명령은 동일하므로 `rp status`와 `research-peer status`는 같습니다. 명시적으로 끄려면 `rp start --no-remote-control --no-auto-answer`를 사용합니다. installer는 기존 `~/.local/bin/rp`를 덮어쓰거나 PATH의 다른 `rp` executable을 가리지 않고, 충돌을 알리며 중단합니다.

Research Peer Channel과 Remote Control이 활성화된 Claude Code가 열립니다. custom Channel이 Anthropic research preview인 동안에는 시작 시 local-development 경고가 표시되며 local owner가 직접 확인해야 합니다. Remote Control은 owner의 Claude account 자격과 organization policy도 충족해야 합니다.

Claude Code 안에서 `/research-peer:`를 입력하면 다음 동작이 자동완성 목록처럼 보입니다.

```text
/research-peer
/research-peer:make
/research-peer:join
/research-peer:ask
/research-peer:handoff
/research-peer:rooms
/research-peer:use
/research-peer:status
/research-peer:leave
/research-peer:delete
/research-peer:auto-answer
/research-peer:update
```

`/research-peer:make`만 입력하고 Enter를 누르면 room 이름을 물어보고, `:join`은 invite를, `:ask`는 질문 내용을 물어봅니다. 답하면 같은 onboarding이 그대로 이어지므로 slash command를 다시 실행할 필요가 없습니다. `make`와 `join`은 daemon/network 상태를 검사하고 안전하게 확정할 수 없는 값만 하나씩 물은 뒤, 필요하면 daemon을 설정·재시작하고 `--endpoint`와 tunnel option도 직접 붙입니다. 먼저 `rp`만 실행하면 되고 endpoint나 daemon을 미리 설정하지 않습니다. plain `/research-peer`는 전체 안내로 계속 사용할 수 있습니다.

활성 room이 하나면 자동 선택됩니다. pairing 후에는 transport 명령을 만들 필요 없이 자연어로 요청합니다.

```text
팀원 Claude에게 toy experiment의 seed와 aggregation 코드를 물어보고,
답이 오면 현재 후속 실험에 반영해줘.
```

## 두 사람 연결

두 연구자 모두 자기 Unix 계정에 설치하고 `rp` 또는 `research-peer`를 실행합니다. `init`이나 daemon 준비는 하지 않습니다. 첫 번째 연구자는 Claude 안에서 room을 만듭니다.

```text
/research-peer:make retrieval-toy
```

일회용 invite를 기존의 안전한 채널로 전달합니다. 두 번째 연구자는 자기 Claude 안에서 가입합니다.

```text
/research-peer:join <invite-code>
```

각 action에서 Claude가 direct private/VPN TCP인지 승인된 SSH tunnel인지 묻고, 확인할 수 없는 address·port·SSH 값만 추가로 묻습니다. endpoint가 포함된 실제 room 명령은 Claude가 실행합니다. 양쪽 owner가 표시된 identity fingerprint를 별도 채널로 확인합니다. 같은 room 이름을 입력했다고 자동 발견하지 않으며, 다른 사용자의 home을 읽지 않고 firewall이나 SSH 설정을 자동 변경하지 않습니다.

양쪽 server가 inbound high port를 drop하고 SSH만 허용한다면 [운영 가이드](docs/operations.md)의 owner-managed 양방향 forwarding 절차를 사용합니다. loopback advertise에는 `--advertise-loopback`을 명시해야 하며 wildcard advertised address는 거부됩니다.

## Inbox, 상태, 선택적 자동 응답

```bash
research-peer inbox
research-peer room status ROOM
research-peer history --room ROOM
research-peer room configure ROOM --auto-answer on --disclosure summary --note 'owner가 승인한 요약'
```

영구적인 room 자동응답 정책은 기본 off입니다. 인자 없는 `rp` 실행 자체가 local owner의 명시적 동의로 취급되어 그 session에만 full 자동답변을 켜며 room 정책은 바꾸지 않습니다. 자동답변 없이 열려면 `research-peer` 또는 `rp start --no-auto-answer`를 사용합니다. 질문이 실제로 배정된 live Claude session에서만 동작하고 daemon 혼자서는 model 답변을 만들 수 없습니다. 기존 room의 `status`, `summary`, `full`, `none` 정책이 있으면 그것이 우선하며, secret·transcript·file content·endpoint·명령/config 변경은 계속 제외됩니다.

## Claude plugin marketplace

저장소에는 `.claude-plugin/marketplace.json` catalog가 포함되어 있습니다. 게시 후 Claude Code 안에서 다음을 실행할 수 있습니다.

```text
/plugin marketplace add ronut01/research-peer
/plugin install research-peer@research-peer-marketplace
```

Marketplace는 namespaced action skill(`/research-peer:make`, `:join`, `:ask`, `:handoff`, `:rooms`, `:use`, `:status`, `:leave`, `:delete`, `:auto-answer`, `:update`, `:peers`, `:help`)과 Channel MCP plugin을 배포합니다. 별도의 user P2P daemon, CLI, systemd user service는 설치하지 않으므로 `./install.sh`를 한 번 실행하거나 위의 에이전트 설치 프롬프트를 사용해야 합니다. 전체 runtime installer는 편의를 위한 plain `/research-peer` personal skill도 설치합니다.

## Claude 안에서 업데이트

2.0을 한 번 설치한 뒤에는 local owner가 다음 action을 명시적으로 호출합니다.

```text
/research-peer:update
```

runtime, plugin, skill을 고정된 공식 GitHub repository 기준으로 업데이트합니다. updater는 checkout identity, Git commit, component version 일치를 검증하고 downgrade를 거부하며 identity, room, history, config와 연구 artifact를 보존합니다. daemon이 이미 실행 중이었을 때만 재시작합니다. 변경 없이 확인하려면 `/research-peer:update check`를 사용합니다. 적용 후 development Channel과 skill을 새로 load하도록 Claude session을 다시 시작해야 합니다. peer message는 update를 실행하거나 승인할 수 없습니다. 이 action이 없는 기존 설치는 검토한 checkout에서 `./install.sh`로 한 번 올려야 합니다.

## Room 나가기와 삭제

`/research-peer:leave`는 해당 room의 local 수신과 대기 중 재시도를 멈추지만 history는 보존합니다. `/research-peer:delete`는 정확한 local 삭제 계획을 먼저 보여주고 local owner에게 명시적인 확인을 받습니다. 확인 후 그 room의 Research Peer message, outbox, invite, membership, counter만 삭제합니다. project repository, experiment artifact, 다른 room, 상대 peer 데이터는 삭제하지 않습니다.

## Remote Control

Remote Control은 peer transport와 자동답변에서 독립적입니다. 인자 없는 `rp`는 Claude global setting이나 room 정책을 바꾸지 않고 해당 session에 Remote Control과 full 자동답변을 opt-in합니다. 둘 다 원하지 않으면 `rp start --no-remote-control --no-auto-answer` 또는 인자 없는 `research-peer`를 사용합니다. peer message는 어느 기능도 활성화할 수 없습니다.

## 제거

먼저 정확한 제거 계획을 확인합니다.

```bash
research-peer uninstall --dry-run
research-peer uninstall
```

기본 제거는 CLI, plugin, personal skill, user service와 Research Peer 전용 config, identity key, rooms, history, outbox, logs, cache, runtime을 제거합니다. project repository, experiment artifact, 관련 없는 Claude 설정·plugin·skill, Remote Control 설정, 상대 peer 데이터는 보존합니다. Research Peer state를 의도적으로 남길 때만 `--keep-data`를 사용합니다.

## 문서

- [제품 사양](docs/product-spec.md)
- [아키텍처](docs/architecture.md)
- [보안 모델](docs/security-model.md)
- [테스트 계획](docs/test-plan.md)
- [서버 환경](docs/server-environment.md)
- [운영 가이드](docs/operations.md)
- [구현 상태](docs/implementation-status.md)
- [에이전트 설치 가이드](docs/agent-install.md)
