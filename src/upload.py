"""CSV/JSON upload parsing and dataset summaries."""
import csv
import io
import json
from pathlib import Path
from fastapi import HTTPException

def read_dataset(filename: str, raw: bytes):
    if len(raw) > 15 * 1024 * 1024: raise HTTPException(413, "حجم الملف الأقصى 15 ميجابايت")
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".csv": rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        elif ext == ".json":
            value = json.loads(raw.decode("utf-8-sig")); rows = value if isinstance(value, list) else value.get("data", [value]) if isinstance(value, dict) else None
        else: raise HTTPException(415, "نقبل ملفات CSV وJSON فقط")
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows): raise ValueError("يجب أن يحتوي الملف على صفوف بصيغة كائنات")
        return rows
    except UnicodeDecodeError: raise HTTPException(400, "احفظ الملف بترميز UTF-8")
    except json.JSONDecodeError: raise HTTPException(400, "ملف JSON غير صالح")
    except ValueError as e: raise HTTPException(400, str(e))

def summarize(rows):
    cols = list(rows[0].keys()); out=[]
    for col in cols:
        vals=[r.get(col) for r in rows if r.get(col) not in (None, "")]
        nums=[]
        for v in vals:
            try: nums.append(float(str(v).replace(",", "")))
            except (ValueError, TypeError): pass
        item={"name":col,"missing":len(rows)-len(vals),"type":"numeric" if vals and len(nums)/len(vals)>.8 else "category","unique":len(set(map(str,vals))),"top":[]}
        if item["type"]=="numeric": item["sum"]=round(sum(nums),2); item["average"]=round(sum(nums)/len(nums),2) if nums else 0
        else:
            counts={}
            for v in vals: counts[str(v)]=counts.get(str(v),0)+1
            item["top"]=[{"value":k,"count":v} for k,v in sorted(counts.items(),key=lambda x:-x[1])[:30]]
        out.append(item)
    return {"rows":len(rows),"columns":out,"preview":rows[:10]}

