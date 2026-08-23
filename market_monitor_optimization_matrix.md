# ETF云端市场监测对象优化评估表 V2.0

验证基准：2026-08-23 多源首轮探针。动作仅调整监测职责，不改变研究池交易规则。

| 对象 | 当前用途 | 当前数据源 | 数据质量 | 是否直接数据 | 是否存在更优替代 | 建议动作 |
|---|---|---|---|---|---|---|
| SOXQ | 半导体指数代理兼ETF对象 | Yahoo | PASS | 否，ETF | SOX直接指数 PASS | DOWNGRADE |
| SOX | 原未进入正式直接层 | Yahoo `^SOX` | PASS，1255行至2026-08-21 | 是 | 是，优于 SOXQ 代理 | UPGRADE |
| NDX | 核心海外指数 | Yahoo `^NDX` | PASS | 是 | 无需替换 | KEEP |
| N225 | 日经ETF背景 | Yahoo `^N225` | PASS | 是 | 优于513520代理 | UPGRADE |
| 513520 | 日经ETF交易对象/指数代理 | hithink ETF | 已实测 | 否，ETF | N225直接指数 | KEEP |
| HSTECH / HS2083 | 恒生科技指数背景 | Yahoo符号失败；官方入口未接入 | 直接长期源未通过 | 目标是直接指数 | 513180仍是可用ETF层 | DOWNGRADE |
| 513180 | 恒生科技ETF交易对象/代理 | hithink ETF | 已实测 | 否，ETF | HS2083未稳定，暂无替代 | KEEP |
| KOSPI | 韩国市场背景 | Yahoo `^KS11` | PASS | 是 | 不新增默认职责 | DOWNGRADE |
| KOSDAQ | 韩国科技背景 | Yahoo `^KQ11` | PASS | 是 | 不新增默认职责 | DOWNGRADE |
| 三星电子 | 半导体个股观察候选 | Yahoo `005930.KS` | DEGRADED，1缺失、1 OHLC异常 | 是，个股 | 无质量更优源 | DOWNGRADE |
| SK海力士 | 半导体个股观察候选 | Yahoo `000660.KS` | DEGRADED，1缺失、3 OHLC异常 | 是，个股 | 无质量更优源 | DOWNGRADE |
| SPX | 新增能力但非现有默认对象 | Yahoo `^GSPC` | PASS | 是 | 不应因可得而扩张 | REMOVE |
| VIX | 新增能力但非现有默认对象 | Yahoo `^VIX` | DEGRADED，49缺失行 | 是 | 不应因可得而扩张 | REMOVE |
| TWII | 非默认亚洲背景 | Yahoo `^TWII` | DEGRADED，1缺失行 | 是 | 不提高当前默认链质量 | KEEP |
| GOLD | 黄金ETF背景 | Yahoo `GC=F` | DEGRADED，缺失行 | 是，商品指标 | 518880 ETF层稳定 | DOWNGRADE |
| DXY | 美元背景 | Yahoo `DX-Y.NYB` | DEGRADED，缺失行 | 是，价格指标 | 无 | DOWNGRADE |
| 561980 | ETF交易候选 | hithink-finance | 已实测 | 否，ETF | 无 | KEEP |
| 588000 | ETF交易候选 | hithink-finance | 已实测 | 否，ETF | 无 | KEEP |
| 159781 | ETF交易候选 | hithink-finance | 已实测 | 否，ETF | 无 | KEEP |
| 159941 | ETF交易候选 | hithink-finance | 已实测 | 否，ETF | NDX为背景指数 | KEEP |
| 159561 | ETF交易候选 | hithink-finance | 已实测 | 否，ETF | 无 | KEEP |
| 518880 | 黄金ETF交易候选 | hithink-finance | 已实测 | 否，ETF | 黄金直接源降级，保留目标层 | KEEP |
| 300750 | 既有打新底仓个股观察 | hithink-finance | 既有源 | 是，个股 | 无 | KEEP |
| 601138 | 既有打新底仓个股观察 | hithink-finance | 既有源 | 是，个股 | 无 | KEEP |

## 动作解释

- 本次没有对象采用 `REPLACE`；该动作保留给未来出现更高质量、稳定直接源且需要整体切换时使用。
- `UPGRADE`：直接数据质量足够，承担指数层职责。
- `DOWNGRADE`：保留对象，但降低为 ETF 观察、代理或条件背景，不承担唯一直接指数职责。
- `REMOVE`：从默认监测链移除，但保留适配器能力和历史数据，不删除文件。
- `KEEP`：维持现有职责或交易对象身份。

HS2083 说明：官方恒生指数页面确认 Hang Seng TECH Index 的正式指数身份和数据下载入口，但本次没有完成可自动化、可追溯的历史 API 接入；Yahoo `HS2083`、`HS2083.HK`、`^HSTECH` 均未通过，`HSTECH.HK` 仅返回1行，不能达到长期监测门槛。
