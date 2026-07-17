"""
多维度模型性能基准测试（实验 6-8 配套代码）

对多个 OpenAI 兼容的 LLM API 提供商，测量以下核心指标：
    - TTFT（Time To First Token，首个 token 到达延迟）
    - 端到端延迟（发出请求到接收完整响应）
    - 吞吐（tokens/s，按生成的输出 token 计）
    - p50 / p95 延迟分位数（方差大意味着体验不稳定）
    - 可用性 / 成功率（失败即计入可用性下降，不中断整表）

实现要点：
    - 使用 openai SDK 的流式接口（stream=True）来精确测量 TTFT。
    - 通过 base_url 复用同一套 OpenAI 兼容协议，适配 Kimi / 豆包等国产 API。
    - 单点请求失败被捕获并记录，不影响同一 (provider, model) 的其它请求，
      也不影响其它 provider —— 这样一次运行就能测出"可用性"这一维度。
"""

from __future__ import annotations

import os
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI


# ---------------------------------------------------------------------------
# 提供商配置
# ---------------------------------------------------------------------------
@dataclass
class ProviderConfig:
    """单个待测 (提供商, 模型) 配置。"""

    name: str                 # 展示名，例如 "OpenAI/gpt-4o-mini"
    model: str                # 传给 API 的模型名
    api_key_env: str          # 读取 API key 的环境变量名
    base_url: Optional[str] = None  # OpenAI 官方留空；其它填各自 base_url

    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env)

    def is_available(self) -> bool:
        """只有 key 存在时才纳入实测。"""
        return bool(self.api_key())


# 默认只跑"手上有有效 key"的三家提供商。
# 需要扩展时，往这里追加 ProviderConfig 即可（例如 DeepSeek 官方 vs SiliconFlow 对比）。
DEFAULT_PROVIDERS: list[ProviderConfig] = [
    # OpenAI 官方（一个 key 测两个模型，观察同厂不同规格的差异）
    ProviderConfig(
        name="OpenAI/gpt-4o-mini",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    ),
    ProviderConfig(
        name="OpenAI/gpt-4o",
        model="gpt-4o",
        api_key_env="OPENAI_API_KEY",
    ),
    # 月之暗面 Kimi（OpenAI 兼容）
    ProviderConfig(
        name="Moonshot/moonshot-v1-8k",
        model="moonshot-v1-8k",
        api_key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.cn/v1",
    ),
    # 字节豆包 / 火山方舟（OpenAI 兼容）
    ProviderConfig(
        name="Doubao/doubao-1.5-pro-32k",
        model="doubao-1-5-pro-32k-250115",
        api_key_env="ARK_API_KEY",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
    ),
]


# ---------------------------------------------------------------------------
# 单次请求测量
# ---------------------------------------------------------------------------
@dataclass
class RequestResult:
    """一次流式请求的测量结果。"""

    ok: bool
    ttft: Optional[float] = None            # 首 token 延迟（秒）
    latency: Optional[float] = None         # 端到端延迟（秒）
    completion_tokens: Optional[int] = None # 生成的输出 token 数
    throughput: Optional[float] = None      # 输出吞吐（tokens/s）
    error: Optional[str] = None             # 失败原因（可用性下降时记录）


def measure_once(
    client: OpenAI,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
) -> RequestResult:
    """
    发起一次流式请求并测量各项指标。

    任何异常都被捕获为一次"失败"，用于统计可用性 —— 绝不向上抛出，
    以免单点故障中断整表测试。
    """
    start = time.perf_counter()
    first_token_at: Optional[float] = None
    completion_tokens = 0
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.0,
            stream=True,
            # 请求用量统计（部分 OpenAI 兼容服务支持；不支持时下方回退到计数）
            stream_options={"include_usage": True},
            timeout=timeout,
        )

        reported_tokens: Optional[int] = None
        for chunk in stream:
            # 首个"有内容"的 chunk 到达时刻即 TTFT
            if chunk.choices:
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    completion_tokens += 1  # 回退计数：以流式 chunk 近似 token 数
            # 若服务在末尾回传了精确 usage，则以其为准
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                reported_tokens = getattr(usage, "completion_tokens", None)

        end = time.perf_counter()

        if first_token_at is None:
            # 拿到了响应但没有任何内容 token，视为失败
            return RequestResult(ok=False, error="empty response (no content token)")

        final_tokens = reported_tokens if reported_tokens else completion_tokens
        latency = end - start
        ttft = first_token_at - start
        # 吞吐按"生成阶段"计：输出 token 数 / (端到端 - 首 token 延迟)
        gen_time = max(latency - ttft, 1e-6)
        throughput = final_tokens / gen_time if final_tokens else 0.0

        return RequestResult(
            ok=True,
            ttft=ttft,
            latency=latency,
            completion_tokens=final_tokens,
            throughput=throughput,
        )
    except Exception as exc:  # noqa: BLE001 —— 故意兜底，任何错误都记为可用性下降
        return RequestResult(ok=False, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 聚合结果
# ---------------------------------------------------------------------------
@dataclass
class ProviderSummary:
    provider: str
    model: str
    total: int
    success: int
    results: list[RequestResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def availability(self) -> float:
        return self.success / self.total if self.total else 0.0

    def _vals(self, attr: str) -> list[float]:
        return [getattr(r, attr) for r in self.results if r.ok and getattr(r, attr) is not None]

    @staticmethod
    def _pct(values: list[float], q: float) -> Optional[float]:
        """线性插值分位数；样本过少时退化为最大/最小值。"""
        if not values:
            return None
        s = sorted(values)
        if len(s) == 1:
            return s[0]
        pos = q * (len(s) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(s) - 1)
        frac = pos - lo
        return s[lo] + (s[hi] - s[lo]) * frac

    def stat(self, attr: str, kind: str) -> Optional[float]:
        vals = self._vals(attr)
        if not vals:
            return None
        if kind == "mean":
            return statistics.mean(vals)
        if kind == "p50":
            return self._pct(vals, 0.50)
        if kind == "p95":
            return self._pct(vals, 0.95)
        raise ValueError(kind)


def benchmark_provider(
    cfg: ProviderConfig,
    prompt: str,
    num_requests: int,
    concurrency: int,
    max_tokens: int,
    timeout: float,
) -> ProviderSummary:
    """对单个提供商发起 num_requests 次请求（并发 concurrency）。"""
    client = OpenAI(api_key=cfg.api_key(), base_url=cfg.base_url)
    results: list[RequestResult] = []

    if concurrency <= 1:
        for _ in range(num_requests):
            results.append(measure_once(client, cfg.model, prompt, max_tokens, timeout))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(measure_once, client, cfg.model, prompt, max_tokens, timeout)
                for _ in range(num_requests)
            ]
            for fut in as_completed(futures):
                results.append(fut.result())

    success = sum(1 for r in results if r.ok)
    errors = [r.error for r in results if not r.ok and r.error]
    return ProviderSummary(
        provider=cfg.name,
        model=cfg.model,
        total=num_requests,
        success=success,
        results=results,
        errors=errors,
    )


def run_benchmark(
    providers: list[ProviderConfig],
    prompt: str,
    num_requests: int,
    concurrency: int,
    max_tokens: int,
    timeout: float,
) -> list[ProviderSummary]:
    """依次对每个提供商跑基准测试（提供商之间串行，单提供商内部并发）。"""
    summaries: list[ProviderSummary] = []
    for cfg in providers:
        print(f"  → 正在测试 {cfg.name} "
              f"(model={cfg.model}, N={num_requests}, 并发={concurrency}) ...", flush=True)
        summary = benchmark_provider(
            cfg, prompt, num_requests, concurrency, max_tokens, timeout
        )
        print(f"    完成：成功 {summary.success}/{summary.total}", flush=True)
        summaries.append(summary)
    return summaries
