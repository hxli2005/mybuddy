# VPet Phase 2 Windows Manual QA

> **历史验收清单**:保留 O1 真机证据口径。v1 的正式完成判定只看
> `VPET_ACCEPTANCE.md`;两者冲突时以后者为准。

本清单用于完成 `docs/VPET_PHASE2_SPEC.md` 第 7 节的 Windows/VPet 实机联调。自动化测试和
跨平台 C# build 只能证明代码可编译,不能替代这些桌宠窗口行为验证。

如果由 Windows 环境里的 Codex 执行,先按
`docs/VPET_PHASE2_WINDOWS_CODEX_RUNBOOK.md` 准备环境、打包、安装、采证,再逐项跑本清单。

## 环境

- Windows 10/11
- VPet-Simulator 当前 Steam 或本地源码版本
- .NET 8 SDK
- MyBuddy 后端:

```bash
uv run mybuddy web --host 127.0.0.1 --port 8000
```

- 插件构建:

```bash
bash scripts/package_vpet_plugin.sh
```

将 `dist/vpet/1114_MyBuddyBridge` 整个目录放到 VPet 本地 MOD 目录,按 VPet 当前 MOD 规则启用。
该目录包含 `info.lps` 和 `plugin/` 子目录,形态对齐官方 VPet.Plugin.Demo。

## 必测流程

1. 状态连通
   - 打开插件设置,URL 使用 `http://127.0.0.1:8000`。
   - token 留空时,后端 `vpet.bridge_token` 也留空。
   - 预期:VPet 状态区域出现 `MyBuddy: bridge connected.`。

2. Token 正确/错误
   - 后端配置 `vpet.bridge_token: "qa-token"` 并重启。
   - 插件填错 token。
   - 预期:状态可见显示 token rejected,UI 不冻结。
   - 插件改成 `qa-token`。
   - 预期:恢复 connected。

3. Chat + thinking
   - 在 VPet TalkBox 选择 MyBuddy,发送一句普通聊天。
   - 预期:发送瞬间播放 thinking/待机反馈;网络慢时窗口仍可操作。
   - 预期:回复以 VPet 气泡显示,动作/表情映射不抛异常。

4. 摸头原生动画
   - 开启后端 `vpet.touch_escalation: true`。
   - 第一次摸头。
   - 预期:VPet 原生摸头动画和台词零感知延迟保留。
   - 预期:额外 MyBuddy 短句可稍后出现,不超过一句短反应。

5. 拖拽不触发
   - 长按并拖动 VPet 窗口。
   - 预期:没有 `touch_head` / `touch_body` 升格气泡。
   - SQL 中不应出现本次拖拽对应的新增触摸升格。

6. 30 秒聚合一次
   - 30 秒内连续摸头 5 次以上。
   - 预期:同一窗口最多一次 LLM 回复。
   - 预期:`vpet_events.client_event_id` 只有一个窗口 id,count 回写为聚合次数。

7. Agent busy 不排队
   - 人为让后端 agent 处于长请求中,同时摸头触发升格。
   - 预期:插件不等待长请求;后端返回 `gate_reason='agent_busy'`。

8. 空闲 drain 停止
   - 准备一条 pending nudge 或 reminder。
   - Windows 无输入超过 `IdlePauseMinutes`。
   - 预期:插件不普通 drain,桌宠不主动弹出。

9. 回场 digest
   - 离开期间积累:过期 reminder、过期 greeting、nudge。
   - 回到电脑。
   - 预期:先记录 `user_back`,再调用 `drain(digest=true)`。
   - 预期:只显示一句 digest;overdue reminder 持久展示;不逐条轰炸。

10. 全屏/演示静默
    - 打开全屏视频或演示模式。
    - 触发 interrupt pending。
    - 预期:不调用走到前台;最多普通气泡。

11. 今天安静
    - 在插件设置里勾选 `今天安静`。
    - 触发 interrupt pending。
    - 预期:不走到前台。
    - 次日重新打开插件或跨天运行。
    - 预期:`今天安静` 自动恢复关闭。

12. 断网/超时
    - 停止 MyBuddy 后端或改错 URL。
    - 发起聊天和 drain。
    - 预期:VPet UI 不冻结,状态显示 unreachable/timeout。

## SQL 证据

触摸升格率:

```sql
select day_index,
       json_extract(server_flags_json, '$.touch_escalation') as touch_on,
       count(*) as events,
       sum(escalated) as escalated,
       sum(replied) as replied
from vpet_events
where event in ('touch_head', 'touch_body')
group by day_index, touch_on;
```

打扰率基础表:

```sql
select day_index, event, gate_reason, context_json, created_at
from vpet_events
where event like 'pending_%'
order by created_at;
```

body_state 矛盾粗筛:

```sql
select day_index, event, body_state_json, context_json, created_at
from vpet_events
where event = 'body_state_conflict'
order by created_at;
```

nudge 响应率基础表:

```sql
select m.id, m.session_id, m.created_at, m.meta_json
from messages m
where json_extract(m.meta_json, '$.source') = 'pending_message'
  and json_extract(m.meta_json, '$.pending_source') in ('nudge', 'dynamic');
```

## 完成判定

全部必测流程通过,且 SQL 能查到 `server_flags_json`、`client_flags_json`、`day_index`、
`pending_discarded`、`pending_digested`、`pending_overdue`、`body_state_conflict`
等关键行后,才可把 Phase 2 spec 视为完成。
