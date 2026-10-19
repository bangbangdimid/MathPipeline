"""Dedup problems: exact (canonical hash) + near-dup (MinHash/Jaccard).

All local & free. Optional semantic dedup via sentence-transformers is a hook
(activates only if the package is installed); MinHash is the default path.
"""
from __future__ import annotations
from collections import defaultdict
from datasketch import MinHash, MinHashLSH
from src.extract.textnorm import normalize, shingles

NUM_PERM = 128
DUP_SIM = 0.90       # >= this -> confirmed_dup
VARIANT_SIM = 0.70   # [VARIANT_SIM, DUP_SIM) -> variant


def _minhash(norm: str) -> MinHash:
    m = MinHash(num_perm=NUM_PERM)
    for sh in shingles(norm):
        m.update(sh.encode('utf-8'))
    return m


def find_duplicates(conn) -> dict:
    rows = conn.execute("SELECT id,statement_latex,canonical_hash FROM problems").fetchall()
    norms, mh, byhash = {}, {}, defaultdict(list)
    for r in rows:
        n = normalize(r["statement_latex"])
        norms[r["id"]] = n
        mh[r["id"]] = _minhash(n)
        byhash[r["canonical_hash"]].append(r["id"])

    conn.execute("DELETE FROM duplicates")  # idempotent recompute
    exact, near = 0, 0
    recorded = set()

    # exact duplicates (identical normalized statement)
    for ids in byhash.values():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = tuple(sorted((ids[i], ids[j])))
                conn.execute(
                    "INSERT INTO duplicates (problem_a,problem_b,similarity,method,verdict) "
                    "VALUES (?,?,?,?,?)", (key[0], key[1], 1.0, "canonical", "confirmed_dup"))
                recorded.add(key)
                exact += 1

    # near-duplicates via LSH
    lsh = MinHashLSH(threshold=VARIANT_SIM, num_perm=NUM_PERM)
    for pid, m in mh.items():
        lsh.insert(str(pid), m)
    for pid, m in mh.items():
        for other in lsh.query(m):
            o = int(other)
            if o == pid:
                continue
            key = tuple(sorted((pid, o)))
            if key in recorded:
                continue
            sim = mh[pid].jaccard(mh[o])
            if sim >= DUP_SIM:
                verdict = "confirmed_dup"
            elif sim >= VARIANT_SIM:
                verdict = "variant"
            else:
                continue
            conn.execute(
                "INSERT INTO duplicates (problem_a,problem_b,similarity,method,verdict) "
                "VALUES (?,?,?,?,?)", (key[0], key[1], float(sim), "minhash", verdict))
            recorded.add(key)
            near += 1
    conn.commit()
    return {"problems": len(rows), "exact_dups": exact, "near_dups": near}
