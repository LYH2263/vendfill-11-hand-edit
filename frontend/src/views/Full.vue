<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const resp = ref<any>({ lanes: [] })
onMounted(async () => { resp.value = await api('/refills/full?location_id=1') })
</script>
<template>
  <h1>满仓</h1>
  <p class="sub">补货单 <strong>#{{ resp.order_id }}</strong> 中补量为 0 且当下缺口为 0 的货道（{{ resp.status === 'void' ? '已作废' : resp.status === 'reconciled' ? '已核销' : '未作废' }}）</p>
  <div class="card">
    <table>
      <thead><tr><th>货道</th><th>商品</th><th>库存</th><th>在途</th><th>容量</th></tr></thead>
      <tbody>
        <tr v-for="l in resp.lanes" :key="l.lane_id">
          <td>{{ l.slot_no }}</td><td>{{ l.sku_name }}</td><td>{{ l.stock }}</td><td>{{ l.in_transit }}</td><td>{{ l.capacity }}</td>
        </tr>
        <tr v-if="!resp.lanes.length"><td colspan="5" class="muted">该单暂无满仓货道</td></tr>
      </tbody>
    </table>
  </div>
</template>
