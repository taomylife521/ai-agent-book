"""
demo.py —— 一条命令跑出多提供商性能对比表。

用法：
    python demo.py                      # 使用默认参数
    python demo.py --num-requests 20 --concurrency 5
    python demo.py --serial             # 串行发送（并发=1）
    python demo.py --list               # 仅列出将要测试的提供商

默认只测"手上有有效 key"的提供商（OpenAI / Kimi / 豆包）。
未设置对应环境变量的提供商会被自动跳过。
"""

from __future__ import annotations

import argparse
import os

# 若安装了 python-dotenv 且存在 .env，则自动加载（可选，不强制）
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from benchmark import DEFAULT_PROVIDERS, ProviderSummary, run_benchmark


# 短 prompt：控制成本，同时保证有稳定的输出用于测吞吐。
DEFAULT_PROMPT = "用一句话解释什么是大语言模型。"


def _fmt(v, unit: str = "", scale: float = 1.0, digits: int = 1) -> str:
    """把可能为 None 的数值格式化为对齐的字符串。"""
    if v is None:
        return "  N/A"
    return f"{v * scale:.{digits}f}{unit}"


def print_table(summaries: list[ProviderSummary]) -> None:
    """打印多维度对比表（TTFT / 端到端 / 吞吐 / p95 / 成功率）。"""
    headers = [
        "Provider/Model",
        "成功率",
        "TTFT均值",
        "TTFT_p95",
        "端到端均值",
        "端到端p95",
        "吞吐",
        "输出tok",
    ]
    rows: list[list[str]] = []
    for s in summaries:
        rows.append([
            s.provider,
            f"{s.success}/{s.total} ({s.availability * 100:.0f}%)",
            _fmt(s.stat("ttft", "mean"), "ms", 1000, 0),
            _fmt(s.stat("ttft", "p95"), "ms", 1000, 0),
            _fmt(s.stat("latency", "mean"), "s", 1, 2),
            _fmt(s.stat("latency", "p95"), "s", 1, 2),
            _fmt(s.stat("throughput", "mean"), " t/s", 1, 1),
            _fmt(s.stat("completion_tokens", "mean"), "", 1, 0),
        ])

    # 计算列宽（中文按 2 字符宽度估算，保证对齐）
    def width(text: str) -> int:
        return sum(2 if ord(c) > 127 else 1 for c in text)

    cols = len(headers)
    col_w = [width(headers[i]) for i in range(cols)]
    for row in rows:
        for i in range(cols):
            col_w[i] = max(col_w[i], width(row[i]))

    def pad(text: str, w: int) -> str:
        return text + " " * (w - width(text))

    sep = "-+-".join("-" * col_w[i] for i in range(cols))
    header_line = " | ".join(pad(headers[i], col_w[i]) for i in range(cols))
    print()
    print(header_line)
    print(sep)
    for row in rows:
        print(" | ".join(pad(row[i], col_w[i]) for i in range(cols)))
    print()

    # 失败明细，便于定位可用性问题
    any_fail = any(s.errors for s in summaries)
    if any_fail:
        print("失败请求明细（可用性下降原因）：")
        for s in summaries:
            if s.errors:
                # 只展示前 3 条，避免刷屏
                for e in s.errors[:3]:
                    print(f"  - {s.provider}: {e}")
                if len(s.errors) > 3:
                    print(f"    ... 以及另外 {len(s.errors) - 3} 条同类错误")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="多维度模型性能基准测试")
    parser.add_argument("--num-requests", type=int, default=10,
                        help="每个提供商的请求次数（默认 10，控制成本）")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="单个提供商内部的并发数（默认 3）")
    parser.add_argument("--serial", action="store_true",
                        help="串行发送（等价于 --concurrency 1）")
    parser.add_argument("--max-tokens", type=int, default=64,
                        help="每次请求生成的最大 token 数（默认 64，控制成本）")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="单次请求超时（秒），超时记为可用性下降")
    parser.add_argument("--prompt", type=str, default=DEFAULT_PROMPT,
                        help="测试用的短 prompt")
    parser.add_argument("--list", action="store_true",
                        help="仅列出将测试的提供商后退出")
    args = parser.parse_args()

    concurrency = 1 if args.serial else args.concurrency

    # 过滤出有 key 的提供商
    available = [p for p in DEFAULT_PROVIDERS if p.is_available()]
    skipped = [p for p in DEFAULT_PROVIDERS if not p.is_available()]

    print("=" * 72)
    print("多维度模型性能基准测试（实验 6-8）")
    print("=" * 72)
    if skipped:
        for p in skipped:
            print(f"[跳过] {p.name} —— 未设置环境变量 {p.api_key_env}")
    if not available:
        print("没有任何可用提供商：请至少设置 OPENAI_API_KEY / MOONSHOT_API_KEY / ARK_API_KEY 之一。")
        return

    print(f"待测提供商：{', '.join(p.name for p in available)}")
    print(f"参数：N={args.num_requests}/家, 并发={concurrency}, "
          f"max_tokens={args.max_tokens}, timeout={args.timeout}s")
    print(f"Prompt：{args.prompt!r}")

    if args.list:
        return

    print("-" * 72)
    summaries = run_benchmark(
        providers=available,
        prompt=args.prompt,
        num_requests=args.num_requests,
        concurrency=concurrency,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
    )

    print_table(summaries)

    print("指标说明：")
    print("  成功率  = 成功请求数 / 总请求数（可用性维度）")
    print("  TTFT    = 首个 token 到达延迟（流式测得），越低越流畅")
    print("  端到端  = 请求发出到响应结束的总耗时")
    print("  吞吐    = 输出 token 数 / 生成阶段耗时（tokens/s）")
    print("  p95     = 95 分位延迟，反映长尾/稳定性（方差大则体验不稳）")


if __name__ == "__main__":
    main()
