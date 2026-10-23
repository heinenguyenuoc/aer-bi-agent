"""Sinh dữ liệu GIẢ (synthetic) mô phỏng cấu trúc dữ liệu upstream oil & gas của Alberta.

Mục đích: chạy được toàn bộ agent ngay, không cần tải dữ liệu thật.
Tên operator là hư cấu. Số liệu sản lượng là mô phỏng (đường suy giảm Arps), KHÔNG phải số thật của AER/Petrinex.

Đơn vị theo quy ước Petrinex/AER:
  dầu & nước: m3 / tháng, khí: e3m3 (= 1000 m3) / tháng.

Chạy:  python scripts/generate_fake_data.py            (ra data/aer.duckdb)
"""
import argparse
import pathlib

import duckdb
import numpy as np
import pandas as pd

ADJ = ["Prairie", "Foothills", "Boreal", "Athabasca", "Cardium", "Peace", "Badlands", "Tamarack",
       "Cinder", "Windmill", "Saddle", "Bowvalley", "Redwillow", "Kestrel", "Muskeg", "Chinook",
       "Coulee", "Sundog", "Ironwood", "Northern Lights", "Birchbark", "Larch", "Highwood", "Pembroke",
       "Grizzly", "Caribou", "Wolverine", "Sable", "Harvest Moon", "Aspen Grove", "Cutbank", "Elkhorn",
       "Stonebridge", "Whitecap Ridge", "Blackfoot", "Spruce Hollow", "Longview", "Ridgeback",
       "Copperline", "Silverleaf"]
SUFFIX = ["Energy Ltd.", "Resources Ltd.", "Petroleum Corp.", "Oil & Gas Ltd.", "Exploration Inc."]
FIELDS = [("Pembina", "Central Alberta"), ("Swan Hills", "Northwest Alberta"), ("Provost", "East Central Alberta"),
          ("Redwater", "Edmonton Area"), ("Wembley", "Peace Country"), ("Cold Lake", "Northeast Alberta"),
          ("Brazeau River", "West Central Alberta"), ("Kaybob", "Northwest Alberta"),
          ("Medicine Hat", "Southeast Alberta"), ("Hussar", "Southern Alberta"),
          ("Willesden Green", "Central Alberta"), ("Ante Creek", "Peace Country")]
HQ = ["Calgary"] * 8 + ["Edmonton"] * 2 + ["Red Deer"]
START, END = "2021-01-01", "2026-08-01"


def main(out: str, seed: int, n_wells: int, n_ops: int) -> None:
    rng = np.random.default_rng(seed)
    months = pd.date_range(START, END, freq="MS")
    T = len(months)

    # ---- operators
    names = [f"{a} {s}" for a in ADJ for s in SUFFIX]
    rng.shuffle(names)
    ops = pd.DataFrame({
        "operator_id": np.arange(1, n_ops + 1),
        "ba_code": ["".join(rng.choice(list("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"), 4)) for _ in range(n_ops)],
        "operator_name": names[:n_ops],
        "hq_city": rng.choice(HQ, n_ops),
    })
    op_w = rng.lognormal(0, 1.1, n_ops)
    op_w /= op_w.sum()

    # ---- fields
    fields = pd.DataFrame({"field_id": np.arange(1, len(FIELDS) + 1),
                           "field_name": [f[0] for f in FIELDS], "region": [f[1] for f in FIELDS]})

    # ---- wells
    fluid = rng.choice(["Oil", "Gas", "Crude Bitumen"], n_wells, p=[0.52, 0.38, 0.10])
    horiz = np.where(fluid == "Crude Bitumen", True, rng.random(n_wells) < 0.55)
    op_id = rng.choice(ops.operator_id.values, n_wells, p=op_w)
    field_id = rng.choice(fields.field_id.values, n_wells)
    # Crude Bitumen chủ yếu ở Cold Lake
    cold = int(fields.loc[fields.field_name == "Cold Lake", "field_id"].iloc[0])
    field_id = np.where((fluid == "Crude Bitumen") & (rng.random(n_wells) < 0.85), cold, field_id)
    spud = pd.to_datetime("2005-01-01") + pd.to_timedelta(rng.integers(0, 365 * 21 + 60, n_wells), unit="D")
    spud = pd.DatetimeIndex(np.minimum(spud.values, np.datetime64("2026-03-01")))
    uwi, seen = [], set()
    while len(uwi) < n_wells:
        u = (f"100/{rng.integers(1, 17):02d}-{rng.integers(1, 37):02d}-{rng.integers(20, 110):03d}-"
             f"{rng.integers(1, 30):02d}W{rng.choice([4, 5, 6])}/00")
        if u not in seen:
            seen.add(u)
            uwi.append(u)
    age_years = (pd.Timestamp(END) - spud).days / 365.25
    p_dead = np.clip((age_years - 6) / 30, 0, 0.6)
    r = rng.random(n_wells)
    status = np.where(r < p_dead * 0.55, "Abandoned", np.where(r < p_dead, "Suspended", "Producing"))
    wells = pd.DataFrame({
        "well_id": uwi, "licence_number": 100000 + np.arange(n_wells), "operator_id": op_id, "field_id": field_id,
        "fluid": fluid, "is_horizontal": horiz, "status": status, "spud_date": spud.date,
        "total_depth_m": np.where(horiz, rng.integers(2400, 5200, n_wells), rng.integers(600, 2600, n_wells)),
    })

    # ---- facilities: gom giếng theo (operator, field, loại) thành các battery tối đa 12 giếng
    wells["_kind"] = np.where(wells.fluid == "Gas", "Gas Battery", "Oil Battery")
    fac_rows, fac_of = [], {}
    n = 0
    opname = ops.set_index("operator_id").operator_name.to_dict()
    fname = fields.set_index("field_id").field_name.to_dict()
    for (o, f, k), g in wells.groupby(["operator_id", "field_id", "_kind"]):
        for i in range(0, len(g), 12):
            n += 1
            fid = f"ABBT{n:07d}"
            fac_rows.append({"facility_id": fid, "facility_name": f"{opname[o].split()[0]} {fname[f]} {k} {i // 12 + 1}",
                             "facility_type": k, "operator_id": o, "field_id": f})
            for w in g.well_id.values[i:i + 12]:
                fac_of[w] = fid
    facilities = pd.DataFrame(fac_rows)
    wells["facility_id"] = wells.well_id.map(fac_of)
    wells = wells.drop(columns="_kind")

    # ---- giá hàng hóa (giả lập, mean-reverting)
    def ar1(mu, sd, phi, lo, hi, x0):
        x = np.empty(T); x[0] = x0
        for t in range(1, T):
            x[t] = mu + phi * (x[t - 1] - mu) + rng.normal(0, sd)
        return np.clip(x, lo, hi)
    price = pd.DataFrame({"production_month": months.date,
                          "wti_usd_bbl": ar1(72, 3.5, 0.92, 48, 105, 70).round(2),
                          "aeco_cad_gj": ar1(2.6, 0.25, 0.9, 1.0, 5.0, 2.8).round(2),
                          "usdcad": ar1(1.36, 0.01, 0.95, 1.25, 1.45, 1.35).round(4)})
    wti_z = ((price.wti_usd_bbl - price.wti_usd_bbl.mean()) / price.wti_usd_bbl.std()).values

    # ---- sản lượng giếng theo tháng (Arps hyperbolic + nhiễu)
    first_idx = ((spud.year - 2021) * 12 + (spud.month - 1)).values + 2   # 2 tháng sau spud mới có sản lượng
    cut = np.full(n_wells, T)                                              # tháng dừng sản xuất
    dead = status != "Producing"
    cut[dead] = np.clip(first_idx[dead] + rng.integers(24, 150, dead.sum()), 0, T)
    qi = np.where(fluid == "Oil", np.where(horiz, rng.uniform(1500, 6000, n_wells), rng.uniform(200, 1200, n_wells)),
         np.where(fluid == "Gas", np.where(horiz, rng.uniform(15000, 80000, n_wells), rng.uniform(3000, 20000, n_wells)),
                  rng.uniform(8000, 25000, n_wells)))
    di = np.where(fluid == "Crude Bitumen", rng.uniform(0.005, 0.02, n_wells), rng.uniform(0.04, 0.14, n_wells))
    b = rng.uniform(0.6, 1.4, n_wells)
    gor = rng.uniform(50, 300, n_wells)          # m3 khí / m3 dầu (giếng dầu)
    cgr = rng.uniform(0.01, 0.05, n_wells)       # m3 condensate / e3m3 khí (giếng khí)
    wc0 = rng.uniform(0.05, 0.4, n_wells)
    wck = rng.uniform(0.002, 0.012, n_wells)

    t = np.arange(T)[None, :] - first_idx[:, None]
    active = (t >= 0) & (np.arange(T)[None, :] < cut[:, None]) & (rng.random((n_wells, T)) > 0.02)
    tt = np.maximum(t, 0)
    q = qi[:, None] / (1 + b[:, None] * di[:, None] * tt) ** (1 / b[:, None])
    q *= rng.lognormal(0, 0.08, (n_wells, T))
    price_f = 1 + 0.08 * wti_z[None, :]
    is_oil, is_gas, is_bit = (fluid == "Oil")[:, None], (fluid == "Gas")[:, None], (fluid == "Crude Bitumen")[:, None]
    oil = np.where(is_oil, q * price_f, np.where(is_gas, q * cgr[:, None], q))
    gas = np.where(is_oil, oil * gor[:, None] / 1000, np.where(is_gas, q, 0.0))
    wc = np.clip(wc0[:, None] + wck[:, None] * tt, 0, 0.95)
    water = np.where(is_bit, oil * rng.uniform(1.5, 3.5, (n_wells, 1)),
                     np.where(is_gas, gas * 0.05, oil * wc / (1 - wc)))
    hours = np.clip(np.round(744 * rng.uniform(0.85, 1.0, (n_wells, T))), 0, 744)

    wi, mi = np.nonzero(active)
    prod = pd.DataFrame({"well_id": np.array(uwi)[wi], "production_month": months.date[mi],
                         "oil_m3": oil[wi, mi].round(1), "gas_e3m3": gas[wi, mi].round(1),
                         "water_m3": water[wi, mi].round(1), "hours_on": hours[wi, mi].astype(int)})

    # ---- facility monthly (tổng từ giếng + flaring/venting), cột giống ST60
    fp = prod.merge(wells[["well_id", "facility_id"]], on="well_id")
    fm = fp.groupby(["facility_id", "production_month"], as_index=False).agg(
        oil_prod_m3=("oil_m3", "sum"), gas_prod_e3m3=("gas_e3m3", "sum"), water_prod_m3=("water_m3", "sum"))
    frac_flare = pd.Series(rng.uniform(0.0, 0.06, len(facilities)), index=facilities.facility_id)
    frac_vent = pd.Series(rng.uniform(0.0, 0.03, len(facilities)), index=facilities.facility_id)
    fm["gas_flared_e3m3"] = (fm.gas_prod_e3m3 * fm.facility_id.map(frac_flare) * rng.uniform(0.7, 1.3, len(fm))).round(1)
    fm["gas_vented_e3m3"] = (fm.gas_prod_e3m3 * fm.facility_id.map(frac_vent) * rng.uniform(0.7, 1.3, len(fm))).round(1)
    for c in ["oil_prod_m3", "gas_prod_e3m3", "water_prod_m3"]:
        fm[c] = fm[c].round(1)

    # ---- ghi DuckDB
    path = pathlib.Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    ddl = {
        "dim_operator": ("operator_id INTEGER, ba_code VARCHAR, operator_name VARCHAR, hq_city VARCHAR", ops),
        "dim_field": ("field_id INTEGER, field_name VARCHAR, region VARCHAR", fields),
        "dim_facility": ("facility_id VARCHAR, facility_name VARCHAR, facility_type VARCHAR, operator_id INTEGER, field_id INTEGER", facilities),
        "dim_well": ("well_id VARCHAR, licence_number INTEGER, operator_id INTEGER, field_id INTEGER, fluid VARCHAR, "
                     "is_horizontal BOOLEAN, status VARCHAR, spud_date DATE, total_depth_m INTEGER, facility_id VARCHAR",
                     wells[["well_id", "licence_number", "operator_id", "field_id", "fluid", "is_horizontal", "status",
                            "spud_date", "total_depth_m", "facility_id"]]),
        "fact_well_production_monthly": ("well_id VARCHAR, production_month DATE, oil_m3 DOUBLE, gas_e3m3 DOUBLE, "
                                         "water_m3 DOUBLE, hours_on INTEGER", prod),
        "fact_facility_monthly": ("facility_id VARCHAR, production_month DATE, oil_prod_m3 DOUBLE, gas_prod_e3m3 DOUBLE, "
                                  "water_prod_m3 DOUBLE, gas_flared_e3m3 DOUBLE, gas_vented_e3m3 DOUBLE",
                                  fm[["facility_id", "production_month", "oil_prod_m3", "gas_prod_e3m3", "water_prod_m3",
                                      "gas_flared_e3m3", "gas_vented_e3m3"]]),
        "fact_commodity_price_monthly": ("production_month DATE, wti_usd_bbl DOUBLE, aeco_cad_gj DOUBLE, usdcad DOUBLE", price),
    }
    for name, (cols, df) in ddl.items():
        con.execute(f"CREATE TABLE {name} ({cols})")
        con.register("_df", df)
        con.execute(f"INSERT INTO {name} SELECT * FROM _df")
        con.unregister("_df")
        print(f"{name:34s} {len(df):>9,d} rows")
    con.close()
    print(f"-> {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/aer.duckdb")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--wells", type=int, default=3000)
    ap.add_argument("--operators", type=int, default=40)
    a = ap.parse_args()
    main(a.out, a.seed, a.wells, a.operators)
