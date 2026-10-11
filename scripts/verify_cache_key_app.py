"""决定性验证：**运行中的安装版 App（冻结的 2.0.0 代码）**算出的缓存 key 与我写入的是否一致。

做法：调用它的「删除结果缓存」端点 —— 该端点用 App 自己的 probe_cache_key 由参数反推 key，
返回 {"deleted": bool, "key": 前 12 位}。若 deleted=true 且 key 前缀等于我写入的，
就证明用户再点一次「开始计算」必然命中这份缓存（否则会白等 70 分钟重算）。

安全性：先备份缓存文件 → 探测（会删掉）→ 立刻还原 → 复核还原成功。
只动这一个缓存文件，不碰作业记录、不碰用户其它数据。
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18765"
CACHE = Path(r"C:\Users\ckx\AppData\Roaming\COF-Film-Recommend\data\dft_cache"
             r"\3dd35e32446f32d73e62fee991079774c7d99fb6.json")
BACKUP = CACHE.with_suffix(".json.probe-backup")
KEY_PREFIX = "3dd35e32446f"

PAYLOAD = {
    "mode": "pair",
    "ald_smiles": "C/N=C/c1c(F)c(F)c(/C=N/c2ccc(-c3nc(-c4ccc(NC)cc4)nc(-c4ccc(NC)cc4)n3)cc2)c(F)c1F",
    "amine_smiles": "O=C(C(OC(F)(F)C(F)(OC(F)(F)C(F)(F)F)F)(F)F)O",
    "backend": "psi4",
    "method": "wb97xd3bj_svp",
    "x_type": "self_stack",
    "with_props": True,
}

PATHS = ["/api/dft/cache/delete", "/api/psi4/cache/delete", "/api/dft/cache/delete/"]


def post(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(PAYLOAD).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"body": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def main() -> int:
    if not CACHE.is_file():
        print("找不到我写入的缓存文件，先确认 seed 是否成功")
        return 2
    shutil.copy2(CACHE, BACKUP)
    print(f"① 已备份缓存 → {BACKUP.name}（{BACKUP.stat().st_size} 字节）")

    print("② 询问安装版 App：这组参数对应哪个 key")
    result = None
    for p in PATHS:
        status, body = post(p)
        print(f"   POST {p} → HTTP {status}  {json.dumps(body, ensure_ascii=False)[:120]}")
        if status == 200:
            result = body
            break
    if result is None:
        shutil.copy2(BACKUP, CACHE)
        print("   ✗ 没找到端点（或调用失败）→ 已还原缓存，未做结论")
        return 1

    ok_key = str(result.get("key") or "") == KEY_PREFIX
    print(f"③ 结论：App 算出的 key 前 12 位 = {result.get('key')}；"
          f"我写入的 = {KEY_PREFIX} → {'一致 ✓' if ok_key else '不一致 ✗'}")
    print(f"   deleted = {result.get('deleted')}（true 表示确实删掉了我写入的那条缓存）")

    shutil.copy2(BACKUP, CACHE)
    restored = CACHE.is_file() and CACHE.stat().st_size == BACKUP.stat().st_size
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    print(f"④ 已还原缓存：{restored}（{CACHE.stat().st_size} 字节）；"
          f"结合能 {data['e_bind_kcal']:.3f} kcal/mol 仍在")
    BACKUP.unlink(missing_ok=True)
    print("\n结果 = " + ("用户点击必然命中缓存 ✓" if ok_key else "key 不一致，需重新落地 ✗"))
    return 0 if ok_key else 1


if __name__ == "__main__":
    raise SystemExit(main())
