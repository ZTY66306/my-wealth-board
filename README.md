# 📊 我的理财收益看板 (My Wealth Board)

全自动个人理财收益监控看板，专为**中国银行**与**招商银行**理财设计，通过 GitHub Actions 每天自动更新最新净值与收益，并通过 GitHub Pages 免费托管，实现手机、电脑打开网页即看最新收益。

---

## 🌟 核心特性

- **真·全自动运行**：基于 GitHub Actions，每天上午 10:30（银行净值披露后）自动抓取并核算收益，无需购买云服务器。
- **双模核算**：
  - **净值型理财**（如招睿青葵、稳富固收）：根据单位净值变动自动核算每日收益与浮盈浮亏。
  - **万份收益型 / 现金管理类**（如朝朝宝、活钱宝）：自动将万份收益滚存计算。
- **现代化看板 UI**：
  - 手机 & 电脑自适应（支持添加到手机桌面作为 Web App）。
  - 红涨绿跌，总资产、今日收益、累计总收益与总收益率一目了然。
  - Chart.js 绘制近 90 日累计收益走势图。
- **双重操作模式**：
  - **小白友好模式**：零 Token，直接编辑 `holdings.json` 即可。
  - **在线管理模式**：配置 GitHub Token 后，直接在网页上搜索添加、删除产品，或一键“立即刷新”。
- **微信播报支持**（可选）：支持 Server酱、PushPlus，每天早上算完直接微信弹窗推送收益明细。

---

## 🚀 部署与启用步骤（只需 3 分钟）

### 第一步：设置 GitHub Actions 读写权限（必做）
GitHub 默认将 Actions 设为只读，必须开启写权限它才能自动把 `data.json` 存回仓库：
1. 进入仓库页面，点击顶部 **Settings**。
2. 左侧菜单找到 **Actions** -> **General**。
3. 滚动到页面下方的 **Workflow permissions**。
4. 勾选 **Read and write permissions**，然后点击 **Save**。

### 第二步：开启 GitHub Pages 网页访问（必做）
1. 在仓库的 **Settings** -> **Pages**。
2. 在 **Build and deployment** 下方的 **Branch**：
   - 分支选择 `main`，目录选择 `/ (root)`。
   - 点击 **Save**。
3. 等待约 1 分钟，刷新页面即可看到你的专属看板访问网址（通常为 `https://<用户名>.github.io/my-wealth-board/`）。

### 第三步：配置你的真实持仓
打开仓库里的 `holdings.json`，点击编辑按钮，填入你实际购买的产品：

```json
[
  {
    "id": "boc_1",
    "bank": "中国银行",
    "name": "中银理财-稳富固收增强",
    "code": "Z7001024000001",
    "type": "nav",
    "shares": 50000.0,
    "principal": 50000.0,
    "initial_nav": 1.0
  },
  {
    "id": "cmb_1",
    "bank": "招商银行",
    "name": "招银理财-朝朝宝",
    "code": "Z7001621000001",
    "type": "ten_thousand",
    "shares": 30000.0,
    "principal": 30000.0,
    "initial_nav": 1.0
  }
]
```
> **如何找到“产品登记编码”？**
> 打开招商银行或中国银行 App，点击你持有的理财产品详情，在“产品概况 / 基础信息 / 销售文件”中找到全国银行业统一定义的 **以 Z7 开头的 14 位登记编码**。

---

## 🔔 可选：配置微信每日收益播报

如果希望每天早上净值计算完成后，微信自动收到今日收益卡片：
1. 获取推送 Key：
   - **Server酱**：访问 [sct.ftqq.com](https://sct.ftqq.com) 扫码登录获取 `SendKey`。
   - 或 **PushPlus**：访问 [pushplus.plus](http://www.pushplus.plus) 微信登录获取 `Token`。
2. 打开仓库 **Settings** -> **Secrets and variables** -> **Actions**。
3. 点击 **New repository secret**：
   - Name 填 `SERVERCHAN_KEY`，Value 填你的 SendKey；
   - 或 Name 填 `PUSHPLUS_TOKEN`，Value 填你的 Token。
4. 之后每天上午定时任务执行完，就会自动往你的微信推送图文报告！

---

## 🛠️ 项目文件结构

```
├── .github/
│   └── workflows/
│       └── sync.yml       # GitHub Actions 自动化定时任务配置
├── holdings.json          # 个人持仓配置文件（仅增删产品或申购赎回时修改）
├── data.json              # 脚本计算后自动生成的收益与历史数据文件
├── sync_wealth.py         # 净值抓取与收益核算核心脚本
├── index.html             # 前端响应式理财收益大屏网页
└── README.md              # 部署与使用文档
```
