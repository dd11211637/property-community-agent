# R-03 消息投递故障

使用虚构消息提供者，将已写入 Outbox 的一条公告消息标记为 `FAILED`。验收：公告仍为
`PUBLISHED`，失败记录包含重试次数和最后错误；相同发布幂等键只能返回初始结果，不能再次入队。

自动化实现见 `tests/announcement/test_announcement_acceptance.py::test_r03_failed_delivery_remains_visible_retryable_and_does_not_republish`。
