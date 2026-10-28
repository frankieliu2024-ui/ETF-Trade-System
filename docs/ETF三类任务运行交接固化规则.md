# ETF三类任务运行交接固化规则

本文件记录今晚已验证并固化到生产链的最小规则，作为实现与回归测试的说明，不替代各任务正式Prompt或所属规范。

## 唯一业务链

ETF正式决策、ETF交易复盘、ETF系统复核各自保持独立业务对象，但共享同一个 Scheduled Review completion owner、同一个 canonical review event/REPORT/notification 交接路径。每次运行只允许一份 FINAL_CONTENT、一份 completion、一份事件和一条通知链。

## 必须逐层确认

以下状态不得互相替代：

1. FINAL_CONTENT 已冻结并完整输出。
2. completion 已写入 main 并回读。
3. 显式通知 workflow 已提交并形成运行。
4. canonical event 和 REPORT 已形成并与同一运行身份、report_id、content_hash、正文绑定。
5. notification_center 已记录本次 provider 结果。
6. PushPlus 服务商已投递。
7. 用户已确认收到。

## 同一 completion 的恢复

正常交接失败时，恢复只能复用同一 completion 路径、同一正文、同一身份和同一 content_hash。恢复不是新建 completion，也不是新建正文或通知链；只有在明确确认需要恢复时，才允许一次有界恢复，并且必须显式传入 force_resend=true，以绕过去重而补发同一通知。

## 去重与补发

普通运行继续按 notification identity 去重。force_resend 只用于同一 completion 的一次有界恢复；不得作为默认路径、不得改变正文、不得改变事件身份、不得创建第二个业务对象。

## 三类任务边界

系统复核不形成交易判断；交易复盘不产生交易许可；正式决策只形成管理建议。共享交接路径不等于共享业务正文、事实或任务身份。

## 业务事实限制

通知链修复不得覆盖业务事实缺口。比如“独立成交与资金流水对账未完成”必须继续作为复盘事实限制披露，不能为了通知闭环改写成“已完成对账”或“无成交”。

## 验收要求

代码变更后必须通过静态回归测试，并在真实运行中分别核对 completion、event、REPORT、notification_center、provider 和用户收件状态。未取得某层真实证据，只能记录未确认。
