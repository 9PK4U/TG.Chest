import type { Cipher } from './types'

const encode = (b: Uint8Array) => btoa(Array.from(b, x => String.fromCharCode(x)).join(''))
const decode = (s: string) => Uint8Array.from(atob(s), c => c.charCodeAt(0))
export const randomSalt = () => encode(crypto.getRandomValues(new Uint8Array(16)))
export const VERIFIER = 'TG.Chest vault v1'
export async function deriveKey(password: string, salt: string): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey'])
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt: decode(salt), iterations: 600000, hash: 'SHA-256' },
    material, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
}
export async function encrypt(key: CryptoKey, text: string): Promise<Cipher> {
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const value = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(text))
  return { v: 1, iv: encode(iv), ciphertext: encode(new Uint8Array(value)) }
}
export async function decrypt(key: CryptoKey, data: Cipher): Promise<string> {
  const value = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: decode(data.iv) }, key, decode(data.ciphertext))
  return new TextDecoder().decode(value)
}
