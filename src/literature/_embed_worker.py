"""embedding worker（v1.9.0）：由 dphuanjing 解释器执行，加载本地模型编码。

输入 JSONL（每行 {"text": "..."}），输出 JSONL（每行 {"vector": [...]}）。
模型默认 BAAI/bge-m3（ModelScope/HF 直下，归一化向量）。

用法（由 src/literature/embedding.py 调用）：
    <dphuanjing python> src/literature/_embed_worker.py --model <name|path> \
        --in <in.jsonl> --out <out.jsonl>
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-m3")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    args = ap.parse_args()

    try:
        from sentence_transformers import SentenceTransformer
        # Qwen3-Embedding 需要 trust_remote_code；bge 等不受影响
        model = SentenceTransformer(args.model, trust_remote_code=True)
    except Exception as exc:
        print(f"worker: 模型加载失败: {exc}", file=sys.stderr)
        sys.exit(2)

    texts = []
    with open(args.in_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                texts.append(json.loads(line).get("text") or "")
            except Exception:
                texts.append(line)

    vectors = model.encode(texts, normalize_embeddings=True,
                           batch_size=8, show_progress_bar=False)
    with open(args.out_path, "w", encoding="utf-8") as f:
        for vec in vectors:
            f.write(json.dumps({"vector": [round(float(x), 6)
                                           for x in vec]}) + "\n")
    print(f"worker: {len(vectors)} vectors", file=sys.stderr)


if __name__ == "__main__":
    main()
