# Trinity Guard — 云端部署方案

> 适用版本：当前开发完成版（pyflakes 0错误 / mypy 0错误）
> 目标环境：Linux 云服务器（Ubuntu 22.04 LTS 推荐）

---

## 一、云服务器配置要求

### 最低配置（模拟盘 / 纸面交易）

| 项目 | 要求 |
|------|------|
| CPU | 4 核（x86_64） |
| 内存 | 8 GB RAM |
| 硬盘 | 50 GB SSD |
| 带宽 | 5 Mbps 以上 |
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.11+ |
| Docker | 24.0+ |

### 推荐配置（实盘 + 回测并行）

| 项目 | 要求 |
|------|------|
| CPU | 8 核（x86_64） |
| 内存 | 16 GB RAM |
| 硬盘 | 200 GB SSD |
| 带宽 | 10 Mbps 以上 |
| 操作系统 | Ubuntu 22.04 LTS |
| Python | 3.11+ |
| Docker | 24.0+ |

> **注意**：同花顺（THS）/ 东方财富（DFCF）客户端依赖 Windows GUI，实盘接入需在 Windows 机器上运行 easytrader，云端只做决策调度，通过 API 下单。

---

## 二、部署架构总览

```
云服务器
├── Docker Compose
│   ├── trinity-guard        ← 主交易系统（src/main.py）
│   ├── one-api              ← LLM 统一网关（端口 3888）
│   └── litellm              ← LLM 代理（端口 4000，供 Cursor 使用）
├── data/                    ← 持久化交易记录 / 回测数据
├── config/                  ← 策略参数 / 模型路由
└── .env                     ← 密钥（不进 Git）

本地 Windows 机器（可选，实盘专用）
└── easytrader + 同花顺/东方财富客户端
    └── 通过 HTTP API 接受云端下单指令
```

---

## 三、部署步骤

### 第一步：服务器初始化

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 安装 Docker Compose
sudo apt install docker-compose-plugin -y

# 安装 Python 3.11
sudo apt install python3.11 python3.11-venv python3.11-pip -y

# 安装常用工具
sudo apt install git vim htop screen -y
```

### 第二步：拉取代码

```bash
git clone <你的仓库地址> /opt/trinity-guard
cd /opt/trinity-guard
```

### 第三步：配置环境变量

```bash
cp .env.example .env
vim .env
```

`.env` 必填项：

```env
# Tushare 数据源（去 tushare.pro 注册获取）
TUSHARE_TOKEN=your_tushare_token_here

# One-API 网关密钥
ONE_API_KEY=your_one_api_key_here
ONE_API_PORT=3888

# LiteLLM 代理
LITELLM_PORT=4000
LITELLM_MASTER_KEY=sk-litellm-trinity
SATORI_API_KEY=your_satori_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

### 第四步：配置券商后端

编辑 `config/broker.yaml`：

```yaml
# 模拟盘阶段（推荐先用这个）
active_broker: local_paper

# 实盘切换后改为：
# active_broker: ths   或   active_broker: dfcf
```

### 第五步：启动服务

```bash
# 构建并启动所有服务
docker compose up -d --build

# 查看运行状态
docker compose ps

# 查看主系统日志
docker compose logs -f trinity-guard

# 查看 One-API 日志
docker compose logs -f one-api
```

### 第六步：验证部署

```bash
# 验证 One-API 网关
curl http://localhost:3888/v1/models \
  -H "Authorization: Bearer your_one_api_key_here"

# 验证 LiteLLM 代理
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer sk-litellm-trinity"

# 验证主系统（查看日志是否正常启动）
docker compose logs trinity-guard | tail -50
```

---

## 四、定时任务配置（Cron）

交易系统需要在工作日盘前自动启动、盘后自动停止：

```bash
# 编辑 crontab
crontab -e
```

添加以下规则：

```cron
# 工作日 9:10 启动 Trinity Guard（提前5分钟准备）
10 9 * * 1-5 cd /opt/trinity-guard && docker compose up -d trinity-guard >> /var/log/trinity.log 2>&1

# 工作日 15:10 停止 Trinity Guard（收盘后10分钟）
10 15 * * 1-5 cd /opt/trinity-guard && docker compose stop trinity-guard >> /var/log/trinity.log 2>&1

# 每天 15:30 执行复盘（DailyReview）
30 15 * * 1-5 cd /opt/trinity-guard && docker compose run --rm trinity-guard python -m src.review.daily_review >> /var/log/trinity-review.log 2>&1
```

---

## 五、数据持久化

`docker-compose.yml` 已挂载以下目录，重启不丢失：

| 容器路径 | 宿主机路径 | 说明 |
|----------|-----------|------|
| `/app/data` | `./data` | 交易记录、模拟盘持仓 |
| `/app/config` | `./config` | 策略参数、模型路由 |
| `/app/.env` | `./.env` | 密钥（只读挂载） |

定期备份建议：

```bash
# 每天凌晨 2:00 备份 data 目录
0 2 * * * tar -czf /backup/trinity-data-$(date +%Y%m%d).tar.gz /opt/trinity-guard/data
```

---

## 六、回测下一步事项

### 阶段一：历史数据准备（第 1-2 周）

```bash
# 安装依赖
pip install tushare akshare pandas

# 拉取历史日线数据（建议至少 3 年）
# 在 src/ 下新建 backtest/data_fetcher.py
# 使用 tushare 拉取：日线 OHLCV、资金流向、龙虎榜、大单数据
```

数据清单：

| 数据类型 | 来源 | 建议时间跨度 |
|----------|------|-------------|
| 日线 OHLCV | tushare / akshare | 2020-至今 |
| 分钟线（1min/5min） | tushare Pro | 近 1 年 |
| 资金流向 | tushare | 2021-至今 |
| 龙虎榜 | tushare | 2020-至今 |
| 新闻舆情 | 自建爬虫 / 同花顺 | 近 2 年 |
| 板块轮动 | akshare | 2020-至今 |

### 阶段二：因子回测（第 3-4 周）

基于 `src/factors/factor_engine.py` 的 A-F 六类因子，逐一验证 IC 值：

```
目标：每个因子的 IC 绝对值 > 0.03，ICIR > 0.5
工具：pandas + scipy（已在 requirements 中）
流程：
  1. 计算每日因子值
  2. 计算 T+1 / T+3 / T+5 收益率
  3. 计算因子 IC（信息系数）
  4. 筛选有效因子，淘汰无效因子
```

### 阶段三：权重模型训练（第 5-6 周）

基于 `src/models/weight_model.py` 的三层盈利目标（+3% / +5% / +8%）：

```
每个盈利目标独立训练：
  - 正样本：持有 N 天内达到目标涨幅
  - 负样本：持有 N 天内未达到目标涨幅
  - 特征：FactorBundle 中的 A-F 因子分数
  - 模型：逻辑回归（基线）→ LightGBM（进阶）
  - 评估：精确率 / 召回率 / AUC，防止过拟合用时间序列交叉验证
```

### 阶段四：模拟盘验证（第 7-12 周）

```
1. 切换 config/broker.yaml → active_broker: local_paper
2. 用真实行情数据跑模拟盘（不实际下单）
3. 每日 DailyReview 自动生成样本，更新权重
4. 观察指标：
   - 胜率 > 55%
   - 盈亏比 > 1.5
   - 最大回撤 < 15%
   - 夏普比率 > 1.0
5. 连续 4 周达标后，考虑切换实盘
```

### 阶段五：实盘切换

```
1. 小仓位试跑（单笔不超过总资金 5%）
2. 观察 2 周，确认系统稳定
3. 逐步放大仓位至 P2 参数上限（单笔最大 20%）
4. 保持每日复盘，权重模型持续进化
```

---

## 七、模型持续进化机制

Trinity Guard 的权重进化依赖 `DailyReview` 每日自动执行：

```
盘后 15:30 触发 DailyReview
  ↓
生成当日多标签样本（+3%/+5%/+8% 三个目标）
  ↓
调用 WeightModel.update_weights() 在线学习
  ↓
L2 正则化防止权重漂移
  ↓
更新 data/weights/ 持久化权重文件
  ↓
次日开盘使用新权重决策
```

---

## 八、安全注意事项

1. **密钥管理**：`.env` 文件绝对不能提交到 Git，已在 `.gitignore` 中排除
2. **防火墙**：只开放必要端口（22/SSH、3888/One-API、4000/LiteLLM），关闭其他端口
3. **实盘资金**：初期严格遵守硬风控（单日亏损 -3% 自动停止，连续 3 天亏损自动停机）
4. **API 限流**：tushare 免费版有调用频率限制，Pro 版建议申请积分提升权限
5. **日志监控**：建议配置告警（如 Telegram Bot），当系统异常停止时及时通知

```bash
# 查看实时日志
docker compose logs -f trinity-guard

# 查看错误日志
docker compose logs trinity-guard | grep ERROR
```

---

## 九、常用运维命令

```bash
# 重启主系统
docker compose restart trinity-guard

# 更新代码后重新部署
git pull
docker compose up -d --build trinity-guard

# 进入容器调试
docker compose exec trinity-guard bash

# 手动触发复盘
docker compose run --rm trinity-guard python -m src.review.daily_review

# 查看资源占用
docker stats

# 清理旧镜像
docker image prune -f
```

---

## 十、推荐云服务商

| 服务商 | 推荐机型 | 月费参考 | 备注 |
|--------|---------|---------|------|
| 阿里云 | ecs.c7.xlarge（4核8G） | ¥200-300 | 国内延迟低，适合 A 股 |
| 腾讯云 | S5.LARGE8（4核8G） | ¥200-300 | 同上 |
| 华为云 | c6.xlarge.2（4核8G） | ¥200-300 | 同上 |

> **强烈建议选国内服务商**：A 股数据源（tushare / akshare）和 LLM API 调用延迟更低，且避免跨境网络不稳定问题。
