<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const refill = ref<any>(null)
const editing = ref<Record<number, number>>({})
const notice = ref('')

async function load() {
  rows.value = await api('/lanes')
  refill.value = await api('/refills/latest?location_id=1')
}
onMounted(load)

function beginStock(r: any) { editing.value[r.id] = r.stock }
async function saveStock(r: any) {
  notice.value = ''
  try {
    await api(`/lanes/${r.id}`, { method: 'PATCH', body: JSON.stringify({ stock: Number(editing.value[r.id]) }) })
    delete editing.value[r.id]
    await load() // 货道库存变化后，补货单冻结补量不变，只刷新当下缺口/满仓判定
  } catch (e: any) {
    notice.value = String(e?.message || e)
  }
}
</script>
<template>
  <h1>货道格子</h1>
  <p class="sub">机面货道网格 · 格内库存条 · 右侧最近补货小票（只看最近单，不另建新单）</p>
  <p v-if="notice" class="sub" style="color:#b3402f">{{ notice }}</p>
  <div class="vf-machine-layout">
    <div class="vf-slot-grid">
      <div v-for="r in rows" :key="r.id" class="vf-slot">
        <div class="vf-slot-no">{{ r.slot_no }}</div>
        <div class="vf-slot-sku">{{ r.sku_name }}</div>
        <div class="vf-slot-bar">
          <div
            class="vf-slot-fill"
            :class="{ 'vf-need': r.gap > 0 }"
            :style="{ width: Math.min(r.fill_pct, 100) + '%' }"
          />
        </div>
        <div class="vf-slot-meta">
          <template v-if="editing[r.id] === undefined">
            {{ r.stock }}/{{ r.capacity }} · 缺 {{ r.gap }}
            <button class="btn" style="padding:0 .4rem;font-size:.66rem" @click="beginStock(r)">改库存</button>
          </template>
          <template v-else>
            <input type="number" min="0" v-model.number="editing[r.id]" style="width:60px" />/{{ r.capacity }}
            <button class="btn" style="padding:0 .4rem;font-size:.66rem" @click="saveStock(r)">存</button>
          </template>
        </div>
      </div>
    </div>
    <aside class="vf-receipt" v-if="refill">
      <h2>*** 补货单 #{{ refill.id }} ***</h2>
      <div class="vf-receipt-line" v-for="l in refill.lines" :key="l.lane_id">
        <span>{{ l.slot_no }} {{ l.sku_name }}<small v-if="l.manual"> ·手改</small></span>
        <span>x{{ l.fill_qty }}</span>
      </div>
      <p class="muted" style="margin:0.75rem 0 0;font-size:0.72rem;color:#6a5e48;text-align:center">
        合计 {{ refill.total_fill }} — 机面打印预览 —
      </p>
    </aside>
  </div>
</template>
