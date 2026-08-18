import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import { channelCapabilities, formatInbound } from '../channel/security.mjs'

test('channel declares injection but never permission relay', () => {
  const capabilities = channelCapabilities()
  assert.deepEqual(capabilities.experimental['claude/channel'], {})
  assert.equal(capabilities.experimental['claude/channel/permission'], undefined)
})

test('inbound provenance clearly marks peer text untrusted', () => {
  const formatted = formatInbound({ body: { text: 'uninstall --yes' } })
  assert.match(formatted, /UNTRUSTED INPUT/)
  assert.match(formatted, /not the local owner/)
  assert.match(formatted, /uninstall --yes/)
})

test('adapter tool surface omits destructive capabilities', async () => {
  const source = await readFile(new URL('../channel/research-peer-channel.mjs', import.meta.url), 'utf8')
  assert.match(source, /research_peer_send/)
  assert.match(source, /research_peer_status/)
  for (const forbidden of ['research_peer_uninstall', 'research_peer_permission', 'research_peer_pair', 'research_peer_delete']) {
    assert.doesNotMatch(source, new RegExp(forbidden))
  }
})

test('real stdio MCP handshake exposes only safe tools and channel capability', t => {
  const requests = [
    { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'research-peer-test', version: '2.0.0' } } },
    { jsonrpc: '2.0', method: 'notifications/initialized' },
    { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }
  ].map(value => JSON.stringify(value)).join('\n') + '\n'
  const child = spawnSync(process.execPath, [new URL('../channel/research-peer-channel.mjs', import.meta.url).pathname], {
    env: { ...process.env, RESEARCH_PEER_CLI: new URL('./fake-research-peer', import.meta.url).pathname, RESEARCH_PEER_POLL_MS: '10000' },
    input: requests, encoding: 'utf8', timeout: 5000
  })
  if (child.error?.code === 'EPERM') {
    t.skip('sandbox blocks nested process creation')
    return
  }
  assert.equal(child.status, 0, child.stderr)
  const responses = child.stdout.trim().split('\n').map(line => JSON.parse(line))
  const initialized = responses.find(message => message.id === 1).result
  const listed = responses.find(message => message.id === 2).result
  assert.deepEqual(initialized.capabilities.experimental['claude/channel'], {})
  assert.equal(initialized.capabilities.experimental['claude/channel/permission'], undefined)
  assert.deepEqual(listed.tools.map(tool => tool.name).sort(), ['research_peer_answer', 'research_peer_send', 'research_peer_status'])
})
