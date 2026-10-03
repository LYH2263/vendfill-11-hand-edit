export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) throw new Error(await errorMessage(res))
  if (res.status === 204) return undefined as T
  return res.json()
}

async function errorMessage(res: Response): Promise<string> {
  const text = await res.text()
  try {
    const j = JSON.parse(text)
    const d = j?.detail
    if (typeof d === 'string') return d
    if (d?.msg) {
      const vs = Array.isArray(d.violations)
        ? '：' + d.violations.map((v: any) => `${v.slot_no} 填 ${v.fill_qty}，超保存当下缺口上限 ${v.max_allowed}`).join('；')
        : ''
      return d.msg + vs
    }
    if (Array.isArray(d) && d[0]?.msg) return d[0].msg
  } catch { /* 非 JSON，原样返回 */ }
  return text || res.statusText
}
