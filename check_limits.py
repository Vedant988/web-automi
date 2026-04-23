"""
check_limits.py
---------------
Makes a tiny Groq API call and prints all rate-limit headers returned by the
server.  Run any time you want a live snapshot of your quota:

    python check_limits.py
    python check_limits.py --model llama-3.1-8b-instant   # cheaper probe
"""

import os
import sys
import argparse
from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Check Groq API rate-limit headers")
    parser.add_argument(
        "--model",
        default="llama-3.1-8b-instant",
        help="Model to probe (default: llama-3.1-8b-instant — cheapest/fastest)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GROQ_API_KEY"),
        help="Groq API key (defaults to GROQ_API_KEY env var)",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: GROQ_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    try:
        from groq import Groq
    except ImportError:
        print("ERROR: groq package not installed.  pip install groq", file=sys.stderr)
        sys.exit(1)

    client = Groq(api_key=args.api_key)

    print(f"Probing model: {args.model}")
    print("Sending minimal request to Groq API …\n")

    try:
        raw = client.chat.completions.with_raw_response.create(
            model=args.model,
            messages=[{"role": "user", "content": "Hi"}],
            max_completion_tokens=1,
            temperature=0.0,
        )
    except Exception as exc:
        print(f"ERROR: API call failed: {exc}", file=sys.stderr)
        sys.exit(1)

    response = raw.parse()
    hdrs = dict(raw.headers)

    # -------------------------------------------------------------------------
    # Rate-limit headers (always present per Groq docs)
    # -------------------------------------------------------------------------
    RATE_HEADERS = [
        ("x-ratelimit-limit-requests",     "RPD limit       (requests/day)"),
        ("x-ratelimit-remaining-requests",  "RPD remaining   (requests/day)"),
        ("x-ratelimit-reset-requests",      "RPD resets in"),
        ("x-ratelimit-limit-tokens",        "TPM limit       (tokens/min)"),
        ("x-ratelimit-remaining-tokens",    "TPM remaining   (tokens/min)"),
        ("x-ratelimit-reset-tokens",        "TPM resets in"),
    ]

    print("-" * 60)
    print(f"  Groq Rate-Limit Snapshot  |  model: {args.model}")
    print("-" * 60)
    for header, label in RATE_HEADERS:
        value = hdrs.get(header, "n/a")
        print(f"  {label:<35}  {value}")
    print("-" * 60)

    # -------------------------------------------------------------------------
    # Token usage for this probe call
    # -------------------------------------------------------------------------
    if response.usage:
        print(f"\n  Probe call token cost:")
        print(f"    Prompt tokens     : {response.usage.prompt_tokens}")
        print(f"    Completion tokens : {response.usage.completion_tokens}")
        print(f"    Total tokens      : {response.usage.total_tokens}")

    # -------------------------------------------------------------------------
    # All other x-ratelimit-* or retry headers (in case Groq adds new ones)
    # -------------------------------------------------------------------------
    extra = {
        k: v for k, v in hdrs.items()
        if k.startswith("x-ratelimit") or k == "retry-after"
        if k not in dict(RATE_HEADERS)
    }
    if extra:
        print("\n  Additional rate-limit headers:")
        for k, v in extra.items():
            print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
