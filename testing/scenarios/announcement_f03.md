# F-03 公告发布闭环

使用虚构小区和成员：客服创建定向 `B1`、`B2` 的检修草稿，提交审核；管理员批准后使用由
后端确认摘要绑定的令牌发布。验收：公告为 `PUBLISHED`，版本、两份受众快照、审核和审计均可
追溯，站内消息 Outbox 只包含快照成员。

自动化实现见 `tests/announcement/test_announcement_acceptance.py::test_f03_customer_service_to_two_buildings_manager_confirms_publication`。
