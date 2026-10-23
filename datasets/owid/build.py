"""Build data/owid.duckdb from the Our World in Data (OWID) energy dataset.

Source: https://github.com/owid/energy-data (owid-energy-data.csv), license CC BY 4.0.
OWID compiles it from the Energy Institute Statistical Review of World Energy, Ember, the US EIA and others.
Cite: "Our World in Data, Energy dataset (CC BY 4.0)".

Run:  python datasets/owid/build.py                 (downloads the CSV if data/owid-energy-data.csv is missing)
"""
import argparse
import pathlib
import urllib.request

import duckdb
import pandas as pd

CSV_URL = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
FIRST_YEAR, LAST_YEAR = 1965, 2023       # 2024+ is only partially reported in this file

# Regional / income-group aggregates worth keeping (OWID also has many overlapping, source-specific aggregates: skipped)
AGGREGATES = ["World", "Africa", "Asia", "Europe", "North America", "South America", "Oceania", "European Union (27)",
              "High-income countries", "Upper-middle-income countries", "Lower-middle-income countries", "Low-income countries"]

ENERGY_COLS = {   # source column -> table column
    "population": "population", "gdp": "gdp_intl_usd", "primary_energy_consumption": "primary_energy_twh",
    "energy_per_capita": "energy_per_capita_kwh", "energy_per_gdp": "energy_per_gdp_kwh",
    "fossil_share_energy": "fossil_share_energy_pct", "renewables_share_energy": "renewables_share_energy_pct",
    "low_carbon_share_energy": "low_carbon_share_energy_pct",
    "coal_production": "coal_production_twh", "oil_production": "oil_production_twh", "gas_production": "gas_production_twh",
    "coal_consumption": "coal_consumption_twh", "oil_consumption": "oil_consumption_twh", "gas_consumption": "gas_consumption_twh",
    "greenhouse_gas_emissions": "ghg_emissions_mt",
}
ELEC_COLS = {
    "electricity_generation": "generation_twh", "electricity_demand": "demand_twh", "net_elec_imports": "net_imports_twh",
    "per_capita_electricity": "per_capita_kwh",
    "coal_electricity": "coal_twh", "gas_electricity": "gas_twh", "oil_electricity": "oil_twh",
    "nuclear_electricity": "nuclear_twh", "hydro_electricity": "hydro_twh", "solar_electricity": "solar_twh",
    "wind_electricity": "wind_twh", "biofuel_electricity": "biofuel_twh", "other_renewable_electricity": "other_renewables_twh",
    "fossil_electricity": "fossil_twh", "renewables_electricity": "renewables_twh", "low_carbon_electricity": "low_carbon_twh",
    "fossil_share_elec": "fossil_share_pct", "renewables_share_elec": "renewables_share_pct",
    "low_carbon_share_elec": "low_carbon_share_pct", "carbon_intensity_elec": "carbon_intensity_gco2_kwh",
}


def _load_csv(csv_path: str | None) -> pd.DataFrame:
    path = pathlib.Path(csv_path or "data/owid-energy-data.csv")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {CSV_URL}")
        with urllib.request.urlopen(CSV_URL, timeout=120) as r:
            path.write_bytes(r.read())
    return pd.read_csv(path)


def main(db_path: str = "data/owid.duckdb", csv_path: str | None = None) -> None:
    raw = _load_csv(csv_path).copy()
    iso = raw["iso_code"].astype("string")
    raw["is_country"] = iso.notna() & ~iso.str.startswith("OWID", na=False)
    keep = raw[(raw["is_country"] | raw["country"].isin(AGGREGATES)) & raw["year"].between(FIRST_YEAR, LAST_YEAR)].copy()

    dim = (keep.groupby("country", as_index=False).agg(iso_code=("iso_code", "first"), is_country=("is_country", "max"))
           .sort_values(["is_country", "country"], ascending=[False, True]))

    def fact(cols: dict) -> pd.DataFrame:
        f = keep[["country", "year", *cols]].rename(columns=cols)
        f = f.dropna(subset=list(cols.values()), how="all")
        f["year"] = f["year"].astype("int32")
        if "population" in f:
            f["population"] = f["population"].round().astype("Int64")
        return f.sort_values(["country", "year"]).reset_index(drop=True)

    energy, elec = fact(ENERGY_COLS), fact(ELEC_COLS)

    path = pathlib.Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    for name, df in [("dim_country", dim), ("fact_energy_yearly", energy), ("fact_electricity_yearly", elec)]:
        con.register("_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _df")
        con.unregister("_df")
        print(f"{name:26s} {len(df):>7,d} rows")
    con.close()
    print(f"-> {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/owid.duckdb")
    ap.add_argument("--csv", default=None, help="local copy of owid-energy-data.csv (downloaded if omitted)")
    a = ap.parse_args()
    main(a.out, a.csv)
