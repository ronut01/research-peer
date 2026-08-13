import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { channelCapabilities, formatInbound } from '../channel/security.mjs'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

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

test('real stdio MCP handshake exposes only safe tools and channel capability', async () => {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [new URL('../channel/research-peer-channel.mjs', import.meta.url).pathname],
    env: {
      ...process.env,
      RESEARCH_PEER_CLI: new URL('./fake-research-peer', import.meta.url).pathname,
      RESEARCH_PEER_POLL_MS: '10000'
    }
  })
  const client = new Client({ name: 'research-peer-test', version: '1.0.0' })
  await client.connect(transport)
  try {
    const capabilities = client.getServerCapabilities()
    assert.deepEqual(capabilities.experimental['claude/channel'], {})
    assert.equal(capabilities.experimental['claude/channel/permission'], undefined)
    const listed = await client.listTools()
    assert.deepEqual(listed.tools.map(tool => tool.name).sort(), ['research_peer_send', 'research_peer_status'])
  } finally {
    await client.close()
  }
})
