<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const data = ref<any>(null)
const editing = ref(false)
const saving = ref(false)
const error = ref('')
const draft = ref<Record<number, number>>({})

// 保存当下缺口允许的最大手改量
const capOf = (l: any) => Math.max(0, l.current_gap ?? l.gap)

function resetDraft() {
  const d: Record<number, number> = {}
  for (const l of data.value?.lines ?? []) {
    // 超缺口冻结行：输入框不得继续显示超缺口正数，预填为当前缺口上限待修复
    d[l.lane_id] = l.over_gap ? capOf(l) : l.fill_qty
  }
  draft.value = d
}

async function load() {
  error.value = ''
  data.value = await api('/refills/latest?location_id=1')
  editing.value = false
  resetDraft()
}

async function run() {
  saving.value = true
  error.value = ''
  try {
    data.value = await api('/refills/run?location_id=1', { method: 'POST' })
    editing.value = false
    resetDraft()
  } catch (e: any) {
    error.value = String(e?.message || e)
  } finally {
    saving.value = false
  }
}

const invalidLines = computed(() => {
  if (!data.value) return []
  return data.value.lines.filter((l: any) => {
    const v = Number(draft.value[l.lane_id])
    return !Number.isInteger(v) || v < 0 || v > capOf(l)
  })
})

const hasBlockedLine = computed(
  () => !!data.value && data.value.lines.some((l: any) => l.over_gap),
)

const draftTotal = computed(() =>
  (data.value?.lines ?? []).reduce((s: number, l: any) => s + (Number(draft.value[l.lane_id]) || 0), 0),
)

async function save() {
  if (!data.value || invalidLines.value.length) return
  saving.value = true
  error.value = ''
  try {
    const lines = data.value.lines.map((l: any) => ({
      lane_id: l.lane_id,
      fill_qty: Number(draft.value[l.lane_id]),
    }))
    data.value = await api(`/refills/${data.value.id}/lines`, {
      method: 'PUT',
      body: JSON.stringify({ lines }),
    })
    editing.value = false
    resetDraft()
  } catch (e: any) {
    // 整次保存失败：页面回退到操作前，与汇总、满仓保持同一套数
    error.value = '保存失败，已整单回退：' + String(e?.message || e)
    await load()
  } finally {
    saving.value = false
  }
}

async function setStatus(action: 'void' | 'verify') {
  if (!data.value) return
  error.value = ''
  try {
    data.value = await api(`/refills/${data.value.id}/${action}`, { method: 'POST' })
    editing.value = false
    resetDraft()
  } catch (e: any) {
    error.value = String(e?.message || e)
  }
}

const statusText = (s: string) => (s === 'open' ? '正常' : s === 'void' ? '已作废' : '已核销')

onMounted(load)
</script>

<template>
  <h1>补货小票</h1>
  <p class="sub">gap = 容量 − 库存 − 在途 · 手改量限 0 至保存当下缺口 · 整单原子保存</p>
  <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
    <button class="btn" :disabled="saving" @click="run">生成补货单</button>
    <button v-if="data?.editable && !editing" class="btn" @click="editing = true">手改补量</button>
    <template v-if="editing">
      <button class="btn" :disabled="saving || invalidLines.length > 0" @click="save">保存手改</button>
      <button class="btn" :disabled="saving" @click="editing = false; resetDraft()">取消</button>
    </template>
    <template v-if="data?.editable && !editing">
      <button class="btn" @click="setStatus('verify')">核销</button>
      <button class="btn" @click="setStatus('void')">作废</button>
    </template>
  </div>
  <p v-if="error" style="color:#b3402a">{{ error }}</p>
  <div style="margin-top:1rem" v-if="data">
    <div class="vf-receipt">
      <h2>*** VendFill 补货单 ***</h2>
      <p style="text-align:center;margin:0;font-size:0.72rem;color:#6a5e48">
        单号 #{{ data.id }} · {{ statusText(data.status) }}
        <span v-if="!data.editable">（禁止手改）</span>
        · 合计 {{ data.total_fill }}
      </p>
      <div class="vf-receipt-line" style="font-weight:700;border-bottom:2px dashed #8a7e64">
        <span>货道 / 商品</span><span>补量</span>
      </div>
      <div class="vf-receipt-line" v-for="l in data.lines" :key="l.lane_id">
        <span>{{ l.slot_no }} {{ l.sku_name }}
          <small>({{ l.status === 'need_fill' ? '待补' : l.status === 'full' ? '满仓' : '超占' }})</small>
          <small v-if="l.manual" style="color:#8a5a00">[手改]</small>
          <small v-if="l.over_gap" style="color:#b3402a">[旧手改 {{ l.fill_qty }} 超当前缺口 {{ capOf(l) }}，已被拦下]</small>
        </span>
        <span v-if="!editing">{{ l.fill_qty }} / 缺{{ l.gap }}</span>
        <span v-else>
          <input
            type="number"
            min="0"
            :max="capOf(l)"
            v-model.number="draft[l.lane_id]"
            :disabled="capOf(l) === 0"
            style="width:4.5rem;text-align:right"
          />
          / 缺{{ l.gap }} · 上限 {{ capOf(l) }}
        </span>
      </div>
      <p v-if="editing" style="text-align:center;margin:0.5rem 0 0;font-size:0.72rem;color:#6a5e48">
        手改合计 {{ draftTotal }}
        <span v-if="hasBlockedLine" style="color:#b3402a">· 含被拦下的超缺口行，修复后方可保存</span>
        <span v-else>· 任一行超缺口将整单回退</span>
      </p>
      <p style="text-align:center;margin:1rem 0 0;font-size:0.72rem;color:#6a5e48">谢谢使用 · 请核对后装机</p>
    </div>
  </div>
</template>
