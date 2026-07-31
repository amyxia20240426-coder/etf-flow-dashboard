# 沪深 ETF 资金流看板

这是可直接发布到 GitHub Pages 的静态网页版，当前展示演示数据，不包含任何 iFinD Token、账号或密码。

## 发布方法

1. 在 GitHub 新建一个 **Public** 仓库，仓库名建议使用 `etf-flow-dashboard`。
2. 点击 **Add file → Upload files**。
3. 上传本文件夹中的 `index.html`、`styles.css`、`script.js` 和 `.nojekyll`，点击 **Commit changes**。
4. 打开 **Settings → Pages**。
5. 在 **Build and deployment** 中选择 **Deploy from a branch**。
6. Branch 选择 `main`，目录选择 `/(root)`，点击 **Save**。
7. 等待约 1–3 分钟，GitHub 会显示网页地址。

## 重要说明

- 当前为演示数据。
- GitHub Pages 只能安全地展示网页，不能把 iFinD Token 写进前端文件。
- 接入 30 分钟实时刷新需要增加一个保管 Token 的后端服务。
