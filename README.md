# 沪深 ETF 资金流看板

这是可直接发布到 GitHub Pages 的沪深 ETF 资金流看板。GitHub Actions
定时更新数据；仓库文件不包含任何 iFinD Token、账号或密码。

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
5. 后续工作日北京时间约每 30 分钟自动更新。

## 重要说明

- 盘中资金流为行情资金流估算，不等同于基金公司次日确认的申赎金额。
- 首次运行后才开始积累历史快照；iFinD 账户未授权的字段会显示待补充。
- 同指数优先使用指数代码归组；暂缺代码时使用标准化指数名称归组并在页面标记。
