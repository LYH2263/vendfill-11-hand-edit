<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const data = ref<any>(null)
const editing = ref(false)
const saving = ref(false)
const draft = ref<Record<number, number>>({})
const dirty = ref<Set<number>>(new Set())
const notice = ref('')

const STATUS_TEXT: Record<string, string> = {
  active: '未作废',
  void: '已作废',
  reconciled: '已核销',
}

function errMsg(e: any): string {
  const raw = String(e?.message || e)
  try { return JSON.parse(raw).detail ?? raw } catch { return raw }
}

async function loadLatest() {
  // latest 只取最近一张，绝不自动生成新单；无单时后端才补建
  data.value = await api('/refills/latest?location_id=1')
}

async function run() {
  notice.value = ''
  data.value = await api('/refills/run?location_id=1', { method: 'POST' })
}

async function startEdit() {
  notice.value = ''
  try {
    // 打开编辑先过服务端闸门：废单/核销单、超缺口冻结手改行一律 409 拦下
    data.value = await api(`/refills/${data.value.id}/edit?location_id=1`)
  } catch (e) {
    // 拦下：不进入编辑，不渲染任何可提交的超缺口正数
    notice.value = '已拦截：' + errMsg(e)
    await loadLatest()
    return
  }
  draft.value = {}
  dirty.value = new Set()
  for (const l of data.value.lines) draft.value[l.lane_id] = l.fill_qty
  editing.value = true
}

function touch(laneId: number, ev: Event) {
  draft.value[laneId] = Number((ev.target as HTMLInputElement).value)
  dirty.value.add(laneId)
}

async function save() {
  if (saving.value) return
  saving.value = true
  notice.value = ''
  // 只提交实际改过的行；整单在服务端统一校验，任一非法即全单失败
  const lines = [...dirty.value].map((lane_id) => ({ lane_id, fill_qty: draft.value[lane_id] }))
  try {
    if (lines.length) {
      data.value = await api(`/refills/${data.value.id}/lines`, {
        method: 'PATCH',
        body: JSON.stringify({ lines }),
      })
    }
    editing.value = false
  } catch (e) {
    // 保存失败：页面整体退回操作前——草稿丢弃，全部行/合计以服务端数据重拉
    notice.value = '保存失败，已全部回退：' + errMsg(e)
    editing.value = false
    await loadLatest()
  } finally {
    saving.value = false
  }
}

function cancelEdit() {
  editing.value = false
  notice.value = ''
  loadLatest() // 放弃草稿，回退到服务端冻结值
}

async function setStatus(action: 'void' | 'reconcile') {
  notice.value = ''
  data.value = await api(`/refills/${data.value.id}/${action}`, { method: 'POST' })
  editing.value = false
}

onMounted(loadLatest)
</script>

<template>
  <h1>补货小票</h1>
  <p class="sub">gap = 容量 − 库存 − 在途 · 手改补量冻结在单 · 收据纸样式</p>
  <div style="display:flex;gap:.5rem;flex-wrap:wrap">
    <button class="btn" @click="run">生成新补货单</button>
    <template v-if="data && data.status === 'active'">
      <button class="btn" v-if="!editing" @click="startEdit">按行手改补量</button>
      <button class="btn" v-if="editing" :disabled="saving" @click="save">保存手改</button>
      <button class="btn" v-if="editing" @click="cancelEdit">放弃</button>
      <button class="btn" @click="setStatus('void')">作废本单</button>
      <button class="btn" @click="setStatus('reconciled')">核销本单</button>
    </template>
  </div>
  <p v-if="notice" class="sub" style="color:#b3402f;margin-top:.5rem">{{ notice }}</p>

  <div style="margin-top:1rem" v-if="data">
    <div class="vf-receipt">
      <h2>*** VendFill 补货单 ***</h2>
      <div class="vf-receipt-line" style="font-size:.72rem;color:#6a5e48;border-bottom:1px dashed #b7ab91">
        <span>#{{ data.id }} · {{ STATUS_TEXT[data.status] ?? data.status }}</span>
        <span>合计 {{ data.total_fill }}</span>
      </div>
      <div class="vf-receipt-line" style="font-weight:700;border-bottom:2px dashed #8a7e64">
        <span>货道 / 商品</span><span>补量</span>
      </div>
      <div class="vf-receipt-line" v-for="l in data.lines" :key="l.lane_id">
        <span>{{ l.slot_no }} {{ l.sku_name }}
          <small>({{ l.status === 'need_fill' ? '待补' : l.status === 'full' ? '满仓' : '超占' }})</small>
          <small v-if="l.manual" style="color:#2f6b4f">·手改</small>
          <small v-if="l.stale" style="color:#b3402f">·已超当下缺口(冻结)</small>
        </span>
        <span v-if="!editing">{{ l.fill_qty }} / 缺{{ l.gap }}</span>
        <span v-else>
          <input
            type="number"
            min="0"
            :max="Math.max(0, l.gap)"
            :value="draft[l.lane_id]"
            @input="touch(l.lane_id, $event)"
            style="width:64px"
          /> / 缺{{ l.gap }}
        </span>
      </div>
      <div class="vf-receipt-line" style="font-weight:700;border-top:2px dashed #8a7e64">
        <span>合计（各行之和）</span><span>{{ data.total_fill }}</span>
      </div>
      <p style="text-align:center;margin:1rem 0 0;font-size:0.72rem;color:#6a5e48">谢谢使用 · 请核对后装机</p>
    </div>
  </div>
</template>
