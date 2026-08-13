# Research Peer

[English](README.md) | **한국어**

Research Peer는 서로 다른 Unix 사용자 또는 연구 서버에서 실행되는 Claude Code끼리 중앙 relay 없이 구조화된 연구 handoff, 후속 질문, 답변, artifact reference를 교환하는 인증 P2P 도구입니다.

인증된 peer 메시지도 항상 신뢰되지 않은 입력으로 취급합니다. 사용자 승인, 위험한 명령 실행 승인, 설정 변경, credential 공개, 추가 pairing, room 삭제 또는 uninstall 승인으로 사용하지 않습니다.

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
평소에는 research-peer 한 단어로 시작한다고 알려줘.
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

## 시작

평소에는 터미널에서 한 단어만 실행합니다.

```bash
research-peer
```

Research Peer Channel이 활성화된 Claude Code가 열립니다. custom Channel이 Anthropic research preview인 동안에는 시작 시 local-development 경고가 표시되며 local owner가 직접 확인해야 합니다.

Claude Code 안에서는 다음처럼 사용합니다.

```text
/research-peer
/research-peer create retrieval-toy
/research-peer join <invite-code>
/research-peer status
/research-peer leave
```

활성 room이 하나면 자동 선택됩니다. pairing 후에는 transport 명령을 만들 필요 없이 자연어로 요청합니다.

```text
팀원 Claude에게 toy experiment의 seed와 aggregation 코드를 물어보고,
답이 오면 현재 후속 실험에 반영해줘.
```

## 두 사람 연결

두 연구자 모두 자기 Unix 계정에 설치하고 `research-peer`를 실행합니다. 첫 번째 연구자는 Claude 안에서 room을 만듭니다.

```text
/research-peer create retrieval-toy
```

일회용 invite를 기존의 안전한 채널로 전달합니다. 두 번째 연구자는 자기 Claude 안에서 가입합니다.

```text
/research-peer join <invite-code>
```

양쪽 owner가 표시된 identity fingerprint를 별도 채널로 확인합니다. 같은 room 이름을 입력했다고 자동 발견하지 않으며, 다른 사용자의 home을 읽지 않고 firewall이나 SSH 설정을 자동 변경하지 않습니다.

## Claude plugin marketplace

저장소에는 `.claude-plugin/marketplace.json` catalog가 포함되어 있습니다. 게시 후 Claude Code 안에서 다음을 실행할 수 있습니다.

```text
/plugin marketplace add ronut01/research-peer
/plugin install research-peer@research-peer-marketplace
```

Marketplace는 namespaced `/research-peer:research-peer` skill과 Channel MCP plugin을 배포합니다. 별도의 user P2P daemon, CLI, systemd user service는 설치하지 않으므로 `./install.sh`를 한 번 실행하거나 위의 에이전트 설치 프롬프트를 사용해야 합니다. 전체 runtime installer는 편의를 위한 plain `/research-peer` personal skill도 설치합니다.

## Remote Control

Remote Control은 선택 사항이며 peer transport와 독립적입니다. `research-peer`로 Claude를 시작한 뒤 기존 Claude Code Remote Control UX를 그대로 사용합니다. Research Peer는 Remote Control을 peer 메시지 transport로 사용하지 않습니다.

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
