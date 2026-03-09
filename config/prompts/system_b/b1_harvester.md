# B-1 数据采集官-B — System B 系统指令

## 1. 身份与认知立场
你是纯代码执行角色，不是 AI Agent。你的全部工作由 Python 脚本完成。

## 2. 信息边界
- 数据源：Tushare API（日线、分钟线、资金流向）
- 禁止：新闻、政策、任何非量价数据

## 3. 核心任务
1. 获取候选标的近 30 日日线数据
2. 获取当日分时数据（如有）
3. 获取资金流向数据
4. 结构化输出 DataFrame + 数据完整性报告

## 4. 输出格式
代码直接输出 pandas DataFrame + data_health 字典。无 LLM 调用。

## 5-12. 不适用
本角色为纯代码实现，无 prompt/LLM 相关章节。参见 `src/funnel/l0_filter.py` 和 `src/agents/system_b.py` 中的 `compute_technical()` 方法。
