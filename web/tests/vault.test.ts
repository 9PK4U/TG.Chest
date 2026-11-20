import { test } from 'node:test'
import assert from 'node:assert/strict'
import { decrypt, deriveKey, encrypt, randomSalt, VERIFIER } from '../src/vault.ts'

test('vault roundtrip, fresh nonces, wrong-password and tamper rejection', async () => {
  const salt = randomSalt()
  const key = await deriveKey('correct horse battery staple', salt)
  const payload = JSON.stringify({ login: 'мой логин', password: 'секрет 🔐', notes: 'личное' })
  const one = await encrypt(key, payload), two = await encrypt(key, payload)
  assert.notEqual(one.iv, two.iv)
  assert.notEqual(one.ciphertext, two.ciphertext)
  assert.equal(await decrypt(key, one), payload)
  assert.equal(await decrypt(key, await encrypt(key, VERIFIER)), VERIFIER)
  const wrong = await deriveKey('another master password', salt)
  await assert.rejects(decrypt(wrong, one))
  const tampered = { ...one, ciphertext: (one.ciphertext[0] === 'A' ? 'B' : 'A') + one.ciphertext.slice(1) }
  await assert.rejects(decrypt(key, tampered))
})
