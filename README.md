# VendFill 售货机补货

按货道容量、库存与在途量计算缺口，生成不超缺口、非负的补货单。

技术栈：Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL / Vue 3 / TypeScript / Vite

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4800 |
| API | http://localhost:9800 |
| API 文档 | http://localhost:9800/docs |
| Postgres | localhost:5449 |

健康检查：`GET http://localhost:9800/api/health`

## 使用说明

1. 在「点位」「货道」查看售货机布局与库存。
2. 在「销量」了解近期出货。
3. 打开「补货单」按缺口生成建议补货量。
4. 已生成且未作废的补货单可「手改补量」：每行限 0 至保存当下缺口，任一行超缺口则整单回退不写半截；作废/核销后禁止手改。手改后缺口若再缩小，超缺口的旧手改行会被拦下，修复后方可保存。
5. 在「满仓」「汇总」查看已满货道与补货合计（与补货单同一套数）。

## 开发与测试

```bash
docker compose exec api pytest -q
```
