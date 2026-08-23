# ETF-Trade-System仓库结构治理报告 V1.0

## 1. 治理范围

本次治理仅调整仓库目录、历史产物归档、运行引用和统一读取入口。未修改交易规则、风险许可、Trial、Confirm、金额规则、卖出规则、研究池或交易权限，也未生成交易建议。

治理对象：`frankieliu2024-ui/ETF-Trade-System`，目标分支：`main`。

## 2. 治理前结构

治理前清单见 `repository_structure_before.md`。四个正式文件位于 `system/`；运行状态位于 `data/state/`；行情快照位于 `data/market/`；回放和历史报告分散在仓库根目录、`backtest_replay/`、`replay_sources/`、`notifications/replay/`。

## 3. 治理后结构

- 规则入口：`docs/rules/ETF规则_MASTER.md`
- 状态入口：`docs/state/ETF当前状态_DASHBOARD.md`
- 复盘入口：`docs/review/ETF交易复盘与经验库_2026.md`
- 行情档案入口：`docs/market/ETF市场行情档案_2026.md`
- 运行状态：`data/state/`
- 行情快照：`data/market/snapshots/`
- 测试与回放：`tests/`、`tests/replay/`、`tests/validation/`
- 历史报告：`archive/reports/`
- 总入口：`ETF_SYSTEM_INDEX.md`

`system/ETF行情数据接口使用规范_V1.0.md`未迁移，因为它不是本次定义的四个正式文件，且当前生产代码不依赖迁移路径。

## 4. 文件迁移与兼容修改

四个正式文件使用 `git mv` 迁移，内容保持不变。报告移动到 `archive/reports/`；回放代码、回放源和通知回放移动到 `tests/replay/`；验证文档移动到 `tests/validation/`。

已同步修正：

- `scripts/state_manager.py` 的 Dashboard 读取路径；
- 状态层和运行模式测试的临时目录路径；
- 回放脚本的仓库根目录定位、源数据路径和结果路径；
- `README.md`、`notifications/README.md` 的入口说明。

GitHub Actions 使用的 `scripts/` 和 `data/` 生产路径保持稳定，无需改动工作流路径。

## 5. 验证结果

- 四个正式文件迁移前后 SHA256 全部一致：通过。
- Python 编译检查：通过。
- `python -m unittest discover -s tests -p "test*.py"`：12 tests，全部通过。
- 市场监测、多源行情、监测优化验证函数：10 项全部通过。
- GitHub Actions 脚本引用存在性检查：通过。
- `git diff --check`：无空白错误；仅有 Windows 换行提示。
- 未执行真实交易，不产生交易建议、不访问账户、不修改交易规则。

本次检查是本地静态与单元验证；推送后仍需由 GitHub Actions 自身产生一次真实 workflow run，才能完成远端运行验证。

## 6. Git记录

本地治理提交：`8345707`（`chore: govern repository structure`）。

本次通过 HTTPS 推送到 `origin/main` 时两次遇到网络连接失败；远端核验显示 `ETF_SYSTEM_INDEX.md` 尚未出现在 GitHub 默认分支。因此当前本地 `main` 领先 `origin/main` 1 个提交，未使用强制推送，也未改写远端历史。

## 7. 结论与未完成事项

本地仓库结构治理完成，正式文件、运行层、测试层和历史层已分离，ChatGPT/Codex/GitHub Actions 的读取和维护路径已在本地 `ETF_SYSTEM_INDEX.md` 中明确。

未完成事项：需要重新建立网络连接后推送 `8345707`，在 GitHub 上确认本次提交后的 Actions workflow 至少成功运行一次；这属于远端运行验证，不是本次目录治理的本地测试结果。
