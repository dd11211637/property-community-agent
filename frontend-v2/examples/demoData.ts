import type { ShowcaseModels } from "../src/models/viewModels";

export const demoModels: ShowcaseModels = {
  workOrders: [
    { id: "wo-1", number: "WX-20260828-018", title: "厨房水槽持续渗水", location: "1 栋 2 单元 1203 · 厨房", status: "IN_PROGRESS", priority: "MEDIUM_RISK", summary: "维修师傅已接单，预计今天 16:30 前上门。", updatedAt: "2026-08-28T12:20:00+08:00" },
    { id: "wo-2", number: "WX-20260827-042", title: "地下车库照明异常", location: "B2 层 C 区", status: "PENDING", priority: "LOW_RISK", summary: "已收集现场信息，等待值班人员确认派单。", updatedAt: "2026-08-28T10:10:00+08:00" },
    { id: "wo-3", number: "WX-20260826-011", title: "门禁卡无法识别", location: "2 号门门禁", status: "COMPLETED", priority: "LOW_RISK", summary: "设备已重新配置，住户确认恢复正常。", updatedAt: "2026-08-27T18:05:00+08:00" },
  ],
  bills: [
    { id: "bill-1", period: "2026-08", total: 430, status: "UNPAID", dueDate: "2026-09-15", items: ["物业费 ¥250", "水电费 ¥30", "停车费 ¥150"] },
    { id: "bill-2", period: "2026-07", total: 398, status: "PAID", dueDate: "2026-08-15", items: ["物业费 ¥250", "水电费 ¥38", "停车费 ¥110"] },
    { id: "bill-3", period: "2026-06", total: 412, status: "PAID", dueDate: "2026-07-15", items: ["物业费 ¥250", "水电费 ¥42", "停车费 ¥120"] },
  ],
  announcements: [
    { id: "an-1", title: "本周电梯例行维保", category: "MAINTENANCE", audience: "1 号楼住户", status: "PUBLISHED", summary: "周六 14:00–16:00 客梯暂停服务，请提前安排出行。", publishedAt: "2026-08-28T09:00:00+08:00" },
    { id: "an-2", title: "秋季邻里市集开始报名", category: "GENERAL", audience: "全社区", status: "PUBLISHED", summary: "欢迎居民申请摊位，也可以报名成为现场志愿者。", publishedAt: "2026-08-27T15:30:00+08:00" },
    { id: "an-3", title: "消防通道联合检查", category: "SAFETY", audience: "3 号楼住户", status: "PUBLISHED", summary: "请勿在楼道与消防通道堆放杂物。", publishedAt: "2026-08-26T11:15:00+08:00" },
  ],
  residents: [{ id: "resident-1", name: "张晓雨", house: "桂语社区 · 1 栋 1203", contact: "138 **** 6721", tags: ["常住", "已认证"] }],
  houses: [
    { id: "house-a", label: "1 栋 2 单元 1203", address: "桂语社区东区", occupancy: "当前房屋" },
    { id: "house-b", label: "6 栋 1 单元 802", address: "桂语社区西区", occupancy: "家庭房屋" },
  ],
  inspections: [
    { id: "in-1", title: "1–3 号楼消防通道巡检", assignee: "周安", status: "IN_PROGRESS", dueAt: "今日 17:00", progress: 68 },
    { id: "in-2", title: "地下车库夜间照明巡检", assignee: "李明", status: "PENDING", dueAt: "今日 20:00", progress: 0 },
    { id: "in-3", title: "公共区域门禁抽检", assignee: "王欣", status: "COMPLETED", dueAt: "今日 12:00", progress: 100 },
  ],
  securityEvents: [
    { id: "se-1", title: "厨房疑似燃气异味", location: "1 栋 1203", risk: "HIGH_RISK", status: "IN_PROGRESS", reportedAt: "2026-08-28T12:42:00+08:00" },
    { id: "se-2", title: "消防通道堆放杂物", location: "3 栋 7 层", risk: "MEDIUM_RISK", status: "PENDING", reportedAt: "2026-08-28T10:15:00+08:00" },
    { id: "se-3", title: "车库出口车辆滞留", location: "南侧出口", risk: "LOW_RISK", status: "CLOSED", reportedAt: "2026-08-27T22:38:00+08:00" },
  ],
  conversations: [
    { id: "cv-1", name: "张晓雨 · 1-1203", preview: "厨房水槽维修什么时候能到？", time: "2 分钟前", unread: 2 },
    { id: "cv-2", name: "陈先生 · 3-706", preview: "消防通道的杂物已经移走了", time: "18 分钟前" },
    { id: "cv-3", name: "王女士 · 6-802", preview: "想咨询停车费明细", time: "1 小时前" },
  ],
  messages: [
    { id: "m-1", sender: "user", body: "厨房水槽维修什么时候能到？", time: "14:02" },
    { id: "m-2", sender: "staff", body: "师傅已经接单，预计 16:30 前上门。我会在到达前再次提醒你。", time: "14:04" },
    { id: "m-3", sender: "user", body: "好的，家里有人。", time: "14:05" },
  ],
  agentResults: [
    { type: "work-order", value: { id: "wo-1", number: "WX-20260828-018", title: "厨房水槽持续渗水", location: "1 栋 2 单元 1203 · 厨房", status: "IN_PROGRESS", priority: "MEDIUM_RISK", summary: "维修师傅已接单，预计今天 16:30 前上门。", updatedAt: "2026-08-28T12:20:00+08:00" } },
    { type: "bill", value: { id: "bill-1", period: "2026-08", total: 430, status: "UNPAID", dueDate: "2026-09-15", items: ["物业费 ¥250", "水电费 ¥30", "停车费 ¥150"] } },
    { type: "suggested-action", label: "联系维修师傅确认到达时间", description: "当前预计 16:30 前上门" },
    { type: "confirmation", title: "发送到达提醒？", description: "将通过统一消息通知当前住户，不会直接改变工单状态。", confirmLabel: "确认发送" },
  ],
};
