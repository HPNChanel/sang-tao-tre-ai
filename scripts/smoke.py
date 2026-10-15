import sys, glob, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.xlsx_tables import load_tables, summarize
d = sys.argv[1] if len(sys.argv) > 1 else "."
for f in sorted(glob.glob(os.path.join(d, "**", "*.xlsx"), recursive=True)):
    ts = load_tables(f)
    print("=" * 10, os.path.basename(f), "tables:", len(ts))
    for t in ts:
        print(" ", t, "header_rows", t.header_rows, "meta", t.meta)
        print("   cols:", [c for c in t.df.columns if not c.startswith("_")])
        print("   paths:", t.df["_row_path"].head(4).tolist(), "...", t.df["_row_path"].tail(2).tolist())
        print("   notes:", len(t.notes), "title:", t.title[:80])
