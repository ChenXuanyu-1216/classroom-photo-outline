"""Opt-in connection test; fixed synthetic inputs, no classroom photos, no retries."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import httpx
from app.config import settings
from app.errors import AppError
from app.services.recognizer import MultimodalRecognizer
from app.services.model_budget import reserve_budget, reserve_request, budget_status, BUDGET_PATH


SYNTHETIC_IMAGE = BACKEND / "tests" / "fixtures" / "deepseek-synthetic-slide.png"
EXPECTED = {"status": "ok", "formula": "P(y|x)", "code": '<input v-model="name">'}


def check_summary(data: dict, kind: str, elapsed: float) -> dict:
    usage = data.get("usage", {})
    estimated = None
    if isinstance(usage, dict):
        incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if type(incoming) is int and type(outgoing) is int and incoming >= 0 and outgoing >= 0:
            estimated = round((incoming * 2 + outgoing * 8) / 1_000_000, 6)
    return {"kind": kind, "request_id": data.get("id"), "model": data.get("model"),
            "usage": usage, "elapsed_seconds": round(elapsed, 3),
            "estimated_cost_cny_upper_rate": estimated}


def run_probe(live: bool = False, vision: bool = False, budget_path: Path = BUDGET_PATH) -> dict:
    key = settings.model_api_key.strip()
    if not live:
        return {"status": "configured_not_called" if key else "missing_key_not_called",
                "model": settings.model_name, "key_present": bool(key), "budget_cap_cny": budget_status()['cap_cny']}
    if not key:
        raise AppError("MODEL_CONFIG_ERROR", "请先注册 DeepSeek 开放平台，并在 backend/.env 填写 API-Key。")
    endpoint = settings.model_base_url.strip().rstrip("/")
    if endpoint not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"} or settings.model_name != "deepseek-flash":
        raise AppError("MODEL_CONFIG_ERROR", "连接测试仅允许 DeepSeek 官方地址和 deepseek-flash 模型。")
    if vision and not SYNTHETIC_IMAGE.is_file():
        raise AppError("MODEL_CONFIG_ERROR", "图片测试需要仓库自带的合成测试图。")

    payload = {"model": "deepseek-flash", "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"}, "max_tokens": 128,
               "messages": [{"role": "user", "content": "请逐字返回这个 JSON 对象：" + json.dumps(EXPECTED)}]}
    reserved = reserve_request(payload, budget_path)
    started = time.monotonic()
    try:
        with httpx.Client(timeout=30, trust_env=settings.model_trust_env) as client:
            response = client.post(f"{endpoint}/chat/completions", json=payload,
                                   headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        message = {401: "密钥认证失败，请检查 API-Key。", 402: "账户余额不足，请检查充值状态。"}.get(status, f"模型服务返回 HTTP {status}，已停止测试。")
        raise AppError("TEST_HTTP_ERROR", message) from None
    except httpx.HTTPError:
        raise AppError("TEST_NETWORK_ERROR", "连接模型服务失败，已停止测试且没有自动重试。") from None
    try:
        data = response.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop" or json.loads(choice["message"]["content"]) != EXPECTED:
            raise ValueError("Unexpected text result")
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise AppError("TEST_OUTPUT_INVALID", "模型返回未通过固定 JSON 样例检查，已停止测试。") from None
    checks = [check_summary(data, "fixed_text_json", time.monotonic() - started)]
    # Save the completed text check even if a later vision check fails.
    report_path = budget_path.with_name("deepseek-last-check.json")
    report_path.write_text(json.dumps({"checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    if vision:
        recognizer = MultimodalRecognizer()
        recognizer.provider = "deepseek"
        recognizer.max_retry = 0
        recognizer.budget_path = budget_path
        # Bound the fixed vision prompt before reserving/sending any request.
        from app.prompts_loader import get_prompt_text
        if len(get_prompt_text("recognize").encode("utf-8")) > 9000 or settings.model_max_tokens > 4096:
            raise AppError("MODEL_CONFIG_ERROR", "图片测试需保持提示词少于9000字节、输出上限不超过4096 Token。")
        result = recognizer.recognize(1, str(SYNTHETIC_IMAGE))
        content = "\n".join(point["content"] for point in result.structured["points"])
        if not all(expected in content for expected in ('P(y|x)', '<input v-model="name">')):
            raise AppError("TEST_OUTPUT_INVALID", "合成图的公式或代码未原样保留，已停止测试。")
        reserved = json.loads(budget_path.read_text(encoding="utf-8"))["reserved_fen"]
        meta = result.structured["_meta"]
        checks.append(check_summary({"id": meta["request_id"], "model": meta["model"], "usage": meta["usage"]},
                                    "synthetic_image", meta["elapsed_seconds"]))
    report = {"status": "passed", "checks": checks, "budget_cap_cny": budget_status()['cap_cny'],
              "reserved_allowance_cny": reserved / 100,
              "note": "预算为保守预留，并非实际扣费；费用估算按核实的高峰单价计算，账单以官方为准。"}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="明确发起一次真实文字调用；默认只检查配置")
    parser.add_argument("--vision", action="store_true", help="文字通过后再调用一次固定合成测试图，不读取课堂照片")
    args = parser.parse_args()
    try:
        report = run_probe(live=args.live, vision=args.vision)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] != "missing_key_not_called" else 2
    except AppError as exc:
        print(json.dumps({"status": "stopped", "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps({"status": "stopped", "code": "TEST_LOCAL_ERROR", "message": "本地测试失败，未自动重试，请检查配置与预算记录。"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
