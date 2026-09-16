#!/bin/bash
# Confirm the parquet the trainer will actually read carries speech tokens and
# embeddings, because make_parquet_list.py wrote its .list files and exited 0
# while pyarrow was missing and no .tar was produced at all.
R=/root/.omnivoice/engines/cosyvoice/CosyVoice
cd "$R/examples/libritts/cosyvoice3"
python3 - <<'PY'
import pyarrow.parquet as pq, glob
for d in ["hk47-train", "hk47-dev"]:
    f = sorted(glob.glob(f"data/{d}/parquet/*.tar"))[0]
    t = pq.read_table(f)
    print(d, "rows", t.num_rows)
    print("   columns:", t.column_names)
    row = {k: t.column(k)[0].as_py() for k in t.column_names}
    for k in ("utt", "spk", "text"):
        if k in row: print(f"   {k}: {str(row[k])[:70]!r}")
    for k in ("speech_token", "utt_embedding", "spk_embedding", "text_token"):
        if k in row and row[k] is not None:
            v = row[k]
            print(f"   {k}: len {len(v)}")
    empty = sum(1 for i in range(t.num_rows)
                if not t.column("speech_token")[i].as_py())
    print("   rows with an EMPTY speech_token:", empty)
PY
