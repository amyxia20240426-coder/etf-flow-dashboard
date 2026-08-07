# 沪深 ETF 资金流看板

这是可直接发布到 GitHub Pages 的沪深 ETF 资金流看板。GitHub Actions
定时更新数据；仓库文件不包含任何 iFinD Token、账号或密码。

## 分板块截至时间

新版数据文件包含 `as_of` 字段。成交额、盘中资金流、涨跌幅、行情市值、
历史趋势、流向结构、指数聚合、管理人排行及异动观察分别显示自己的最近
成功更新时间，统一转换为北京时间。非交易时段保留最近一次成功快照，
不会误显示为正在实时更新。

## 双资金流口径

- 盘中资金流：公开行情的成交方向估算，每 30 分钟刷新。
- 日度净申赎：iFinD ETF 份额日变化 × 当日单位净值，显示上一有效交易日、
  最近 5 个交易日以及近 1 月/3 月/1 年趋势。

首次成功运行时自动回填近一年，此后只补最近两周。历史份额频率不足、覆盖率不足、
单位异常或接口无权限时会明确提示，不会使用盘中估算冒充日度净申赎。

## 发布方法

1. 在 GitHub 新建一个 **Public** 仓库，仓库名建议使用 `etf-flow-dashboard`。
2. 点击 **Add file → Upload files**。
3. 上传本文件夹中的 `index.html`、`styles.css`、`script.js` 和 `.nojekyll`，点击 **Commit changes**。
4. 打开 **Settings → Pages**。
5. 在 **Build and deployment** 中选择 **Deploy from a branch**。
6. Branch 选择 `main`，目录选择 `/(root)`，点击 **Save**。
7. 等待约 1–3 分钟，GitHub 会显示网页地址。

## 启用自动数据

1. 在仓库 `Settings → Secrets and variables → Actions` 中创建
   `IFIND_REFRESH_TOKEN`。
2. 上传 `.github/workflows/update-etf-data.yml`、`update_data.py`、
   `requirements.txt` 和 `data` 文件夹。
3. 打开仓库 `Actions → Update ETF data → Run workflow`。
4. 第一次选择完整资料刷新；成功后网页自动读取全量数据。
5. 后续盘中任务在工作日北京时间 09:00–15:30 约每 30 分钟运行；
   日终任务在工作日 18:30 更新确认后的每日净申赎历史。
6. 已打开的网页每 5 分钟自动检查一次新数据，切回浏览器标签页时也会立即检查。

## 重要说明

- 盘中资金流为行情资金流估算，不等同于基金公司次日确认的申赎金额。
- 各板块显示自己的数据截至时间；接口失败时会明确标记“沿用上次成功数据”。
- 首次运行后才开始积累历史快照；iFinD 账户未授权的字段会显示待补充。
- 同指数优先使用指数代码归组；暂缺代码时使用标准化指数名称归组并在页面标记。
