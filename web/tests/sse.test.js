import test from 'node:test'
import assert from 'node:assert/strict'

import { parseSseChunk } from '../src/lib/sse.js'

test('parseSseChunk parses multiple events and preserves remainder', () => {
  const input = 'event: start\ndata: {"ok":true}\n\nevent: delta\ndata: {"chunk":"你"}\n\npartial'
  const parsed = parseSseChunk('', input)

  assert.equal(parsed.events.length, 2)
  assert.equal(parsed.events[0].event, 'start')
  assert.deepEqual(parsed.events[0].data, { ok: true })
  assert.equal(parsed.events[1].event, 'delta')
  assert.deepEqual(parsed.events[1].data, { chunk: '你' })
  assert.equal(parsed.remainder, 'partial')
})
