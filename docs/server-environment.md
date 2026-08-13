# Maintainer 검증 서버 환경 — public redacted record

조사일: 2026-08-13 KST  
방법: read-only shell/CLI, 승인된 host-level network/service 조회, Anthropic 공식 문서 조회. credential/private key/token 내용은 읽거나 기록하지 않았다.

추적 문서에는 “실제 개인 서버 주소를 Git에 commit하지 않는다”는 제품 원칙에 따라 host IP 마지막 octet과 SSH target을 기록하지 않는다. exact value는 `research-peer doctor --json`의 local-only 출력으로 재확인한다.

## 현재 서버에서 직접 확인한 사실

### OS와 사용자

- Ubuntu 22.04.5 LTS (Jammy), kernel `6.8.0-136-generic`, `x86_64`
- hostname, Unix user, uid/gid는 public repository에서 redacted. shell `/bin/bash`, home은 `$HOME`으로 표기한다.
- passwordless `sudo -n` 불가; v1은 sudo를 요구하지 않는다.
- build workspace는 writable했다. 구현 sandbox에서는 home write가 제한됐지만 승인된 user-scope 설치는 실제 `$HOME`에서 성공했다.
- filesystem ext4, 916 GiB 중 약 92% 사용, 약 72 GiB available. `quota` command는 설치되지 않아 per-user quota는 확인 불가.
- Git 2.34.1. 최초 build workspace는 writable Git metadata가 제공되지 않아 public publication은 sanitized staging checkout에서 수행했다.
- Python 3.10.12, Node 22.22.2, npm 10.9.7, `uv` 존재. `python`, `pip3`, Bun은 없음. npm prefix는 user NVM 아래이므로 user-scope Node package가 가능하다. Python core는 stdlib로 설치한다.

### service

- host-level `systemctl --user`는 running이며 user bus 연결 가능.
- `loginctl`: `Linger=yes`, user state active. user unit이 SSH logout 뒤에도 유지될 조건이 있다.
- tmux 3.2a 설치, screen 미설치.
- `$XDG_RUNTIME_DIR`에 user runtime tree와 Unix sockets가 존재. 구현된 doctor의 create/connect/cleanup 검사가 `UNIX_SOCKET_OK`로 통과했다.
- high TCP ports 여러 개가 이미 loopback 및 wildcard에서 사용 중이다. 구현된 doctor가 OS-assigned port에서 `LOCAL_BIND_OK`, `LOOPBACK_OK`로 통과했다.

### network

- wired interface UP, public-routed IPv4 `/24`; interface 이름, subnet, host address와 gateway는 public repository에서 redacted.
- `lo`는 `127.0.0.1/8`, docker0는 link down.
- 일반 routing table에는 VPN/tunnel/private lab route가 보이지 않는다. 같은 사설망/VPN 연결 근거는 확인되지 않았고 public routed LAN으로 보인다.
- ssh client OpenSSH 8.9p1/OpenSSL 3.0.2 설치.
- system SSH service active, sshd listener 실행 중. 일반 user 조회 범위에서 host가 SSH server임을 확인했다.
- `~/.ssh`에서 기록 가능한 configured peer Host alias를 찾지 못했다. 다른 연구 server 주소/계정은 추가 정보가 필요하다.
- outbound HTTPS는 `code.claude.com` 200, `api.anthropic.com` endpoint 응답으로 확인했다.
- UFW는 일반 user로 상태 조회 불가, nftables/iptables도 root 권한이 필요해 firewall 규칙은 확인 불가.
- listening ports의 일부는 wildcard, 일부 loopback이며 Research Peer는 기존 service port를 재사용하지 않는다.
- 실제 다른 server inbound/outbound, direct TCP, one-way 여부는 peer endpoint 없어서 미검증이다.

### Claude Code

- native Claude Code 2.1.231, linux-x64. 이 작업 중 auto-update로 2.1.229에서 2.1.231로 바뀌었음을 직접 확인했다.
- executable `$HOME/.local/bin/claude`; native version payload `$HOME/.local/share/claude/versions/2.1.231`
- `claude doctor`: install issue 없음, auto update latest channel.
- auth status는 logged in, `claude.ai`, first-party provider이며 Channel/Remote Control eligibility를 충족했다. subscription tier, email/org identifier는 public record에 남기지 않는다.
- plugin CLI와 skills-directory plugin 지원 확인; Research Peer skills-directory plugin이 설치되고 enabled/loaded 상태다.
- MCP CLI 지원, configured claude.ai connectors가 health check에 연결됨(구체 host/account는 기록하지 않음).
- Remote Control command/flag 존재하며 `claude doctor`가 Remote Control section을 정상 표시. 현재 로그인 방식은 공식 prerequisite인 full claude.ai subscription login과 일치한다. 실제 session 생성/모바일 접속은 owner opt-in이 필요해 수행하지 않았다.
- hidden `--channels`와 `--dangerously-load-development-channels`를 `--version`과 함께 실행해 이 binary가 두 option을 parse함을 확인했다.
- system managed settings의 일반 Linux 경로에서 Research Peer가 확인한 파일은 없었다. 현재 로그인에서는 실제 development Channel session 두 개가 시작됐으므로 이 계정에 대한 Channel startup 차단은 관찰되지 않았다. 다른 조직 정책은 별도다.
- `/research-peer`는 `~/.claude/skills/research-peer/SKILL.md` personal skill로, Channel/MCP는 `.claude-plugin/plugin.json`이 있는 skills-directory plugin으로 제공한다.
- 실제 plugin inventory가 `.mcp.json`의 Channel server 1개와 12개 skill(research-peer overview + make/join/ask/handoff/rooms/use/status/leave/delete/peers/help)을 발견했다. interactive `/mcp`에서 connected/2 tools로 표시됐고 실제 `/research-peer help`도 plain command로 실행됐다.

## 공식 문서로 확인한 외부 기능

- Channels는 research preview이며 claude.ai 또는 Console API auth가 필요하고 Team/Enterprise는 `channelsEnabled` opt-in이 필요하다.
- custom Channel은 same-machine stdio MCP server, `claude/channel` capability, `notifications/claude/channel` event contract다.
- custom development Channel은 `--dangerously-load-development-channels server:...` 또는 `plugin:...`로 시작한다. flag는 preview 동안 help에 숨겨진다.
- `.mcp.json`에 있다는 것만으로 Channel injection은 안 되며 session start opt-in이 필요하다.
- plugin MCP server는 enable 시 자동 시작하고 `/reload-plugins`가 connection을 갱신할 수 있다. Channel을 이미 열린 session에 새로 opt-in할 수 있다는 보장은 없다.
- plugin skills/tools는 scoped name을 사용한다. personal skill directory는 plain `/research-peer` UX를 제공한다.
- Remote Control은 claude.ai/code/mobile에서 local process를 조작하며 claude.ai subscription login이 필요하다. process 종료 또는 약 10분 network outage에서 끝날 수 있다. 모바일 push는 focus/presence 등에 따라 생략될 수 있다.

## 아직 검증되지 않은 설계 가정

- 선택한 interface/high port가 peer server에서 inbound 허용됨
- 두 server 사이 routing/firewall/VPN이 direct TCP를 허용함
- 반대 방향 endpoint도 도달 가능함
- peer account가 user service, Python/OpenSSL/Node를 사용할 수 있음
- 조직 server-side policy가 custom Channels와 Remote Control을 허용함
- actual SSH target/authorization이 있어 SSH fallback probe를 할 수 있음
- SSH logout 뒤 Research Peer service가 장시간 실제 유지됨(linger 조건만 확인)

## 구현 후 현재 설치 상태

- **[SERVER-VERIFIED]** Research Peer 1.1.0을 user scope에 설치하고 user daemon을 재시작했다. 기존 identity fingerprint는 유지됐고 room/peer/outbox는 0이었다.
- CLI, personal skill, skills-directory plugin, user service, XDG config/state/cache, install manifest가 생성됐다.
- user service는 enabled이며 smoke 동안 systemd user mode로 시작해 loopback port에 bind한 뒤 중지했다. 최종 상태는 inactive/stopped다.
- 실제 기본 `research-peer uninstall --dry-run`이 program owned entry와 config/state/cache/runtime의 exact removal plan을 표시했으며 mutation은 없었다.
- 실제 identity fingerprint는 tracked 문서에 고정하지 않는다. `research-peer status`에서 local 확인한다.

## 재현용 read-only 명령

```text
research-peer doctor
claude --version
claude doctor
claude auth status --text
systemctl --user is-system-running
loginctl show-user "$USER" -p Linger -p State
ip -brief address
ip route
ss -lntup
```

출력을 공유할 때 email, org ID, exact public address, SSH target, token/key를 redaction한다.
