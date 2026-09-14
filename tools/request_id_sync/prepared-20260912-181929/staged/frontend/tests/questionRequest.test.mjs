import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequestId, ensureRequestId } from '../src/utils/questionRequest.ts'

test('new operations get distinct UUID v4 values', () => {
  const ids = Array.from({ length: 100 }, createRequestId)
  assert.equal(new Set(ids).size, ids.length)
  for (const id of ids) {
    assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  }
})

test('transport retries keep the ID on the same operation', () => {
  const operation = {}
  const first = ensureRequestId(operation)
  assert.equal(ensureRequestId(operation), first)
  assert.equal(operation.request_id, first)
  assert.notEqual(ensureRequestId({}), first)
})

test('caller-provided ID is not replaced', () => {
  assert.equal(ensureRequestId({ request_id: 'client-1' }), 'client-1')
})
