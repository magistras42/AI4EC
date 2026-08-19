#!/usr/bin/env python3
"""Embedding-based lemma ranking — the method under evaluation.

For a goal, rank every Top_* environment lemma in its SMT-LIB dump:
  query          = instruction + raw goal text (smtcore.extract_goal)
  documents      = the lemma's assert bodies (smtcore.collect_env_asserts)
  score(lemma)   = max cosine over its bodies
No LLM call anywhere; needs only the dump EasyCrypt already produces.

Subcommands (one per evaluation axis):
  rank33     rank the 33 strip-hints failing goals (workdir/failing_goals.json)
             -> ranked top-32 preds json, scored online by replay_eval kablation
  recall266  rank all labeled eval goals (eval/eval.jsonl) against the FULL
             env of their bare-smt dumps (default stripped_workdir/dumps,
             pool median ~1.5k) -> fullenv_<tag>.json (full-recall@k: does
             the top-k contain the entire known core out of ~1.5k?) and
             preds_evalcands_<tag>.json (the eval candidate list re-ranked
             by the same scores).
             NOTE: pass --dumpdir replay_dumps_backup to rank the actually
             TRANSMITTED env of the labeled (hinted) calls instead — that
             small-pool (~20) ranking is what the portfolio arm pins
             (eval/preds_armpicks_qe4.json), not a retrieval metric.

Usage (HF env, GPU):
  python embed_rank.py rank33 --workdir stripped_workdir \
      --model Qwen/Qwen3-Embedding-4B --out eval/preds_embed_top32_qe4.json
  python embed_rank.py recall266 --model Qwen/Qwen3-Embedding-4B
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from smtcore import collect_env_asserts, extract_goal
from replay_eval import SOLVERS, find_dump, load_dump_index

INSTR = ("Instruct: Given an SMT goal, retrieve the axioms (lemmas) an SMT "
         "solver needs to prove it.\nQuery: ")

CONFIGS = {
    "sentence-transformers/all-MiniLM-L6-v2":
        dict(pooling="mean", instr="", max_len=512, batch=256),
    "BAAI/bge-m3":
        dict(pooling="cls", instr="", max_len=1024, batch=128),
    "Qwen/Qwen3-Embedding-0.6B":
        dict(pooling="last", instr=INSTR, max_len=1024, batch=64),
    "Qwen/Qwen3-Embedding-4B":
        dict(pooling="last", instr=INSTR, max_len=1024, batch=32),
    "Qwen/Qwen3-Embedding-8B":
        dict(pooling="last", instr=INSTR, max_len=1024, batch=32),
}

TAGS = {  # short names for output files
    "sentence-transformers/all-MiniLM-L6-v2": "minilm",
    "BAAI/bge-m3": "bgem3",
    "Qwen/Qwen3-Embedding-0.6B": "qe06",
    "Qwen/Qwen3-Embedding-4B": "qe4",
    "Qwen/Qwen3-Embedding-8B": "qe8",
}

KS = (1, 2, 4, 8, 16, 32)


class Embedder:
    """Per-model pooling/config so MiniLM (mean), bge-m3 (CLS) and the
    Qwen3-Embedding family (last-token, instruction on query) all rank
    with the encoding their authors intended. Cosine = dot of L2-normed.
    eos=True appends EOS before last-token pooling (official Qwen3 recipe;
    ablated WORSE than pooling the last content token — see paper 4.3)."""

    def __init__(self, model_name, eos=False):
        import torch
        from transformers import AutoModel, AutoTokenizer
        cfg = CONFIGS[model_name]
        self.torch = torch
        self.pooling = cfg["pooling"]
        self.instr = cfg["instr"]
        self.max_len, self.batch = cfg["max_len"], cfg["batch"]
        self.eos = eos and self.pooling == "last"
        side = "left" if self.pooling == "last" else "right"
        self.tok = AutoTokenizer.from_pretrained(model_name,
                                                 padding_side=side)
        self.model = AutoModel.from_pretrained(
            model_name, torch_dtype=torch.bfloat16).cuda().eval()

    def _tokenize(self, chunk):
        if self.eos:
            ids = self.tok(chunk, truncation=True,
                           max_length=self.max_len - 1)["input_ids"]
            ids = [x + [self.tok.eos_token_id] for x in ids]
            return self.tok.pad({"input_ids": ids}, padding=True,
                                return_tensors="pt").to("cuda")
        return self.tok(chunk, padding=True, truncation=True,
                        max_length=self.max_len,
                        return_tensors="pt").to("cuda")

    def encode(self, texts, is_query=False):
        torch = self.torch
        if is_query and self.instr:
            texts = [self.instr + t for t in texts]
        embs = []
        with torch.no_grad():
            for i in range(0, len(texts), self.batch):
                enc = self._tokenize(texts[i:i + self.batch])
                h = self.model(**enc).last_hidden_state
                if self.pooling == "last":
                    v = h[:, -1]
                elif self.pooling == "cls":
                    v = h[:, 0]
                else:
                    m = enc["attention_mask"].unsqueeze(-1)
                    v = (h * m).sum(1) / m.sum(1).clamp(min=1)
                embs.append(torch.nn.functional.normalize(v.float(), dim=-1))
        return torch.cat(embs)


def score_lemmas(emb, goal, bodies, owner, cache=None):
    """{lemma name: max cosine(goal, body) over its bodies}.

    cache maps body text -> embedding, shared across goals (dumps of one
    file repeat the same env axioms thousands of times)."""
    q = emb.encode([goal], is_query=True)
    if cache is None:
        d = emb.encode(bodies)
    else:
        new = [b for b in bodies if b not in cache]
        if new:
            for b, v in zip(new, emb.encode(new)):
                cache[b] = v
        d = emb.torch.stack([cache[b] for b in bodies])
    sims = (d @ q.T).squeeze(-1).tolist()
    best = {}
    for name, s in zip(owner, sims):
        best[name] = max(best.get(name, -2.0), s)
    return best


def flatten_env(env, names=None):
    """(bodies, owner) lists in `names` order (default: env insertion)."""
    bodies, owner = [], []
    for name in (env if names is None else names):
        for b in env[name]:
            bodies.append(b)
            owner.append(name)
    return bodies, owner


# ---------------------------------------------------------------- rank33

def cmd_rank33(args):
    ids = json.load(open(Path(args.workdir) / "failing_goals.json"))
    emb = Embedder(args.model, eos=args.eos)
    preds = {}
    for i, rec in enumerate(sorted(ids, key=lambda r: r["id"])):
        text = Path(rec["dump"]).read_text(errors="replace")
        env = collect_env_asserts(text)
        names = sorted(env)
        if not names:
            preds[rec["id"]] = []
            continue
        bodies, owner = flatten_env(env, names)
        best = score_lemmas(emb, rec["goal"], bodies, owner)
        ranked = sorted(names, key=lambda k: -best[k])
        preds[rec["id"]] = [k[4:] for k in ranked[:32]]
        print(f"[{i + 1}/{len(ids)}] {rec['id']} pool={len(names)} "
              f"asserts={len(bodies)}", flush=True)
    json.dump(preds, open(args.out, "w"), indent=1)
    print(f"wrote {args.out}")


# ------------------------------------------------------------- recall266

def rank_eval_goal(rec, idx, emb, cache):
    """Rank one eval.jsonl goal against its full dump env; None if no dump."""
    p = find_dump(idx, rec["file"], rec["solver"], rec["call"], rec["goal"])
    if p is None:
        for s in SOLVERS:
            p = find_dump(idx, rec["file"], s, rec["call"], rec["goal"])
            if p is not None:
                break
    if p is None:
        return None
    text = p.read_text(errors="replace")
    env = collect_env_asserts(text)
    if not env:
        return None
    bodies, owner = flatten_env(env)
    return score_lemmas(emb, extract_goal(text), bodies, owner, cache)


def cmd_recall266(args):
    tag = TAGS[args.model] + ("eos" if args.eos else "")
    ev = [json.loads(l) for l in open(HERE / "eval" / "eval.jsonl")]
    idx = load_dump_index(args.dumpdir)
    emb = Embedder(args.model, eos=args.eos)

    preds, fullenv, cache = {}, {}, {}
    n_nodump = n_cand_nobody = 0
    for i, rec in enumerate(ev):
        best = rank_eval_goal(rec, idx, emb, cache)
        if best is None:
            n_nodump += 1
            continue
        # pool = eval.jsonl candidates (display names, Top_ stripped)
        scored = []
        for c in rec["candidates"]:
            s = best.get("Top_" + c)
            if s is None:
                n_cand_nobody += 1
                s = -1e9
            scored.append((-s, c))
        scored.sort()
        preds[rec["id"]] = [c for _, c in scored[:16]]
        # pool = full env
        ranked = sorted(best, key=lambda n: -best[n])
        ans = {"Top_" + a for a in rec["answer"]}
        row = {"id": rec["id"], "pool": len(best),
               "ans_in_env": int(ans <= set(best))}
        for k in KS:
            row[f"full@{k}"] = int(ans <= set(ranked[:k]))
        fullenv[rec["id"]] = row
        if (i + 1) % 40 == 0:
            print(f"[{i + 1}/{len(ev)}]", flush=True)

    write_recall_outputs(tag, ev, preds, fullenv, n_nodump, n_cand_nobody)


def write_recall_outputs(tag, ev, preds, fullenv, n_nodump, n_cand_nobody):
    out1 = HERE / "eval" / f"preds_evalcands_{tag}.json"
    out2 = HERE / "eval" / f"fullenv_{tag}.json"
    json.dump(preds, open(out1, "w"), indent=1)
    json.dump(fullenv, open(out2, "w"), indent=1)

    n = len(fullenv)
    pools = sorted(r["pool"] for r in fullenv.values())
    print(f"\n{tag}: scored {n}/{len(ev)} goals "
          f"(no dump {n_nodump}, cand-without-body {n_cand_nobody})")
    print(f"fullenv pool median {pools[n // 2]}, max {pools[-1]}; "
          f"answer-in-env {sum(r['ans_in_env'] for r in fullenv.values())}/{n}")
    print("fullenv full-recall@k: " + "  ".join(
        f"@{k}={sum(r[f'full@{k}'] for r in fullenv.values()) / n:.3f}"
        for k in KS))
    print(f"wrote {out1.name}, {out2.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rank33")
    p.add_argument("--workdir", required=True)
    p.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    p.add_argument("--out", default="eval/preds_embed_top32.json")
    p.add_argument("--eos", action="store_true")
    p.set_defaults(fn=cmd_rank33)

    p = sub.add_parser("recall266")
    p.add_argument("--model", required=True)
    p.add_argument("--dumpdir",
                   default=str(HERE / "stripped_workdir" / "dumps"))
    p.add_argument("--eos", action="store_true")
    p.set_defaults(fn=cmd_recall266)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
