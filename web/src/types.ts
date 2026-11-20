export type Cipher = { v: 1; iv: string; ciphertext: string }
export type Vault = { salt: string; verifier: Cipher; hint?: string }
export type AIPolicy = 'external' | 'trusted' | 'local'
export type Folder = { id: string; name: string; parent_id: string | null; color: string }
export type Attachment = { id: string; filename: string; size: number; mime: string; status: string }
export type Entry = { id: string; title: string; kind: 'note' | 'link' | 'secret'; body?: string; preview: string; secret?: Cipher;
  folder_id: string | null; tags: string[]; favorite: boolean; created_at: number; updated_at: number; revision: number;
  attachments: Attachment[]; index_status: string; source: string; ai_policy: AIPolicy; capture_id?: string | null }
export type Me = { id: number; name: string; vault: Vault | null; bot_ai_policy: AIPolicy; integrations: { storage: string; embeddings: boolean; llm: boolean; trusted_llm: boolean } }
export type Capture = { id: string; mode: string; policy: AIPolicy; status: string; created_at: number; result_ids: string[];
  instruction?: string; selected?: string; replacement?: string; error: string;
  original?: { title: string; body: string; revision: number }; attachments?: { id: string; filename: string }[] }
export type SearchResult = { answer: string | null; warnings: string[]; mode: string;
  results: { id: string; title: string; excerpt: string; kind: string; folder_id: string | null }[] }
export type TelegramApp = { initData: string; ready: () => void; expand: () => void; colorScheme?: string;
  platform?: string; isFullscreen?: boolean; isVersionAtLeast?: (version: string) => boolean;
  requestFullscreen?: () => void; exitFullscreen?: () => void;
  onEvent?: (event: string, handler: (data?: { error?: string }) => void) => void;
  offEvent?: (event: string, handler: (data?: { error?: string }) => void) => void;
  setHeaderColor?: (c: string) => void; setBackgroundColor?: (c: string) => void; openLink?: (url: string) => void;
  HapticFeedback?: { impactOccurred: (style: string) => void } }
declare global { interface Window { Telegram?: { WebApp: TelegramApp } } }
