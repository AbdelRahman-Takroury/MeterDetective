"""Isolated, credential-free adaptation of Developer A's ReAct prototype.

This experiment reads prepared fixture files. It is not imported by the API.
"""

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

DEFAULT_DATA_DIR = Path("data/processed/rebuilt_developer_a")


def get_transformer_data(transformer_id: str, data_dir: Path) -> list[dict[str, Any]]:
    """Return observed daily averages; anomaly injection happens in fixture generation."""
    by_day: dict[str, list[float]] = defaultdict(list)
    with (data_dir / "transformer_readings.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["Transformer_ID"] == transformer_id:
                by_day[row["DateTime"][:10]].append(float(row["Transformer_Reading"]))
    return [
        {"day": day, "average_input_kwh": mean(readings), "reading_count": len(readings)}
        for day, readings in sorted(by_day.items())
    ]


def get_tariff_info(data_dir: Path) -> dict[str, Any]:
    return json.loads((data_dir / "jod_tariff.json").read_text(encoding="utf-8"))


def get_historical_alerts(transformer_id: str, data_dir: Path) -> list[dict[str, str]]:
    with (data_dir / "historical_alerts.csv").open(encoding="utf-8", newline="") as stream:
        return [row for row in csv.DictReader(stream) if row["Equipment_ID"] == transformer_id]


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_transformer_data",
                "description": "Get observed daily transformer energy readings",
                "parameters": {
                    "type": "object",
                    "properties": {"transformer_id": {"type": "string"}},
                    "required": ["transformer_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_tariff_info",
                "description": "Get the synthetic JOD tariff fixture",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_historical_alerts",
                "description": "Get historical tickets for a transformer",
                "parameters": {
                    "type": "object",
                    "properties": {"transformer_id": {"type": "string"}},
                    "required": ["transformer_id"],
                },
            },
        },
    ]


def run_agent(data_dir: Path, transformer_id: str, model: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Set GROQ_API_KEY in the environment to run this experiment")
    try:
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError("Install the optional groq package to run this experiment") from exc

    client = Groq(api_key=api_key)
    messages: list[Any] = [
        {
            "role": "system",
            "content": (
                "Investigate observed transformer readings. Use tools for every numerical or "
                "historical claim. State uncertainty and do not infer ground-truth labels."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Investigate transformer {transformer_id} and summarize its readings and history."
            ),
        },
    ]
    for _ in range(8):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=_tool_schemas(),
            tool_choice="auto",
            temperature=0,
        )
        message = response.choices[0].message
        messages.append(message)
        if not message.tool_calls:
            return message.content or "No conclusion returned"
        for call in message.tool_calls:
            name = call.function.name
            arguments = json.loads(call.function.arguments or "{}")
            if name == "get_transformer_data":
                result = get_transformer_data(arguments["transformer_id"], data_dir)
            elif name == "get_tariff_info":
                result = get_tariff_info(data_dir)
            elif name == "get_historical_alerts":
                result = get_historical_alerts(arguments["transformer_id"], data_dir)
            else:
                result = {"error": f"Unknown tool: {name}"}
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
            )
    raise RuntimeError("Experiment exceeded its eight-turn limit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Developer A's isolated agent prototype")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--transformer", default="TX_3")
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--offline", action="store_true", help="Show tool data without an API call")
    args = parser.parse_args(argv)
    if args.offline:
        print(
            json.dumps(
                {
                    "transformer_data": get_transformer_data(args.transformer, args.data_dir),
                    "tariff": get_tariff_info(args.data_dir),
                    "historical_alerts": get_historical_alerts(args.transformer, args.data_dir),
                }
            )
        )
        return 0
    print(run_agent(args.data_dir, args.transformer, args.model))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
