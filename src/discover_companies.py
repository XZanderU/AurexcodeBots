"""Descubre empresas candidatas desde Datos Abiertos Colombia (Socrata).
No extrae datos de propietarios/personas naturales ni afirma que sean oportunidades verificadas.
Ejecutar desde la raíz de AurexcodeBots: python .\src\discover_companies.py
"""
import csv
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
EXPORTS_DIR = ROOT / "exports"
DB_PATH = DATA_DIR / "aurexcode.db"
DATASET_ID = "nb3d-v3n7"
BASE = f"https://www.datos.gov.co/resource/{DATASET_ID}.json"
META = f"https://www.datos.gov.co/api/views/{DATASET_ID}.json"
LIMIT = 300
TIMEOUT = 30

# Bogotá + municipios principales de la Sabana. Se amplía después según resultados.
TARGET_MUNICIPALITIES = [
    "BOGOTA D.C.", "BOGOTÁ D.C.", "BOGOTA, D.C.",
    "CHIA", "CHÍA", "CAJICA", "CAJICÁ", "COTA", "FUNZA",
    "MOSQUERA", "MADRID", "SOACHA", "ZIPAQUIRA", "ZIPAQUIRÁ",
    "FACATATIVA", "FACATATIVÁ", "TOCANCIPA", "TOCANCIPÁ",
    "SOPÓ", "SOPO", "LA CALERA", "TENJO", "TABIO", "GACHANCIPA", "GACHANCIPÁ"
]


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n"))


def pick_field(columns, choices):
    by_norm = {norm(c.get("fieldName") or c.get("name")): c.get("fieldName") for c in columns}
    for choice in choices:
        if norm(choice) in by_norm:
            return by_norm[norm(choice)]
    for key, actual in by_norm.items():
        if any(norm(choice) in key for choice in choices):
            return actual
    return None


def main():
    DATA_DIR.mkdir(exist_ok=True)
    EXPORTS_DIR.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "AurexcodeBots/1.0 (public business data research)"})
    try:
        meta_response = session.get(META, timeout=TIMEOUT)
        meta_response.raise_for_status()
        columns = meta_response.json().get("columns", [])
        fields = {
            "name": pick_field(columns, ["razon_social", "nombre_de_la_entidad", "nombre_establecimiento", "nombre"]),
            "municipality": pick_field(columns, ["municipio", "municipio_comercial", "mun_comercial"]),
            "department": pick_field(columns, ["departamento"]),
            "status": pick_field(columns, ["estado_matricula", "est_matricula"]),
            "renewed": pick_field(columns, ["ultimo_ano_renovado", "ult_ano_ren", "ultimo_ano_renovado"]),
            "ciiu": pick_field(columns, ["cod_ciiu_act_econ_pri", "ciiu_1", "ciiu"]),
            "activity": pick_field(columns, ["actividad", "descripcion_actividad_economica"]),
        }
        if not fields["name"] or not fields["municipality"]:
            print("No se pudieron identificar los campos de nombre y municipio en el conjunto de datos.")
            print("Campos detectados:", fields)
            sys.exit(2)

        select_fields = list(dict.fromkeys(v for v in fields.values() if v))
        municipality_field = fields["municipality"]
        # SoQL uses field identifiers from the dataset metadata.
        where_parts = [f"upper({municipality_field})='{m.upper().replace(chr(39), chr(39)*2)}'" for m in TARGET_MUNICIPALITIES]
        params = {
            "$select": ", ".join(select_fields),
            "$where": "(" + " OR ".join(where_parts) + ")",
            "$limit": LIMIT,
            "$order": (fields["renewed"] + " DESC") if fields["renewed"] else fields["name"],
        }
        response = session.get(BASE, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        rows = response.json()
    except requests.RequestException as exc:
        print(f"No se pudo consultar Datos Abiertos Colombia: {exc}")
        print("No se modificó la base de datos.")
        sys.exit(1)

    # Guardamos solo datos empresariales básicos. No consultamos ni exportamos datos de propietarios.
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS company_candidates (
        candidate_key TEXT PRIMARY KEY,
        company_name TEXT NOT NULL,
        municipality TEXT,
        department TEXT,
        registration_status TEXT,
        last_renewed_year TEXT,
        economic_activity_code TEXT,
        activity_description TEXT,
        source_name TEXT NOT NULL,
        source_url TEXT NOT NULL,
        collected_at TEXT NOT NULL,
        review_status TEXT NOT NULL DEFAULT 'pendiente_verificacion'
    )""")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    saved = 0
    for row in rows:
        name = str(row.get(fields["name"], "")).strip()
        municipality = str(row.get(fields["municipality"], "")).strip()
        if not name or not municipality:
            continue
        ciiu = str(row.get(fields["ciiu"], "")).strip() if fields["ciiu"] else ""
        key = norm(name) + "|" + norm(municipality)
        values = (
            key, name[:200], municipality[:100],
            str(row.get(fields["department"], "")).strip()[:100] if fields["department"] else "",
            str(row.get(fields["status"], "")).strip()[:100] if fields["status"] else "",
            str(row.get(fields["renewed"], "")).strip()[:20] if fields["renewed"] else "",
            ciiu[:30],
            str(row.get(fields["activity"], "")).strip()[:300] if fields["activity"] else "",
            "Datos Abiertos Colombia / Confecámaras",
            f"https://www.datos.gov.co/d/{DATASET_ID}", now,
        )
        conn.execute("""INSERT INTO company_candidates (
            candidate_key, company_name, municipality, department, registration_status,
            last_renewed_year, economic_activity_code, activity_description,
            source_name, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_key) DO UPDATE SET
            company_name=excluded.company_name, department=excluded.department,
            registration_status=excluded.registration_status,
            last_renewed_year=excluded.last_renewed_year,
            economic_activity_code=excluded.economic_activity_code,
            activity_description=excluded.activity_description,
            source_name=excluded.source_name, source_url=excluded.source_url,
            collected_at=excluded.collected_at""", values)
        saved += 1
    conn.commit()

    output = EXPORTS_DIR / "company_candidates.csv"
    columns_out = ["company_name", "municipality", "department", "registration_status",
                   "last_renewed_year", "economic_activity_code", "activity_description",
                   "source_name", "source_url", "collected_at", "review_status"]
    all_rows = conn.execute(
        "SELECT " + ", ".join(columns_out) + " FROM company_candidates ORDER BY municipality, company_name"
    ).fetchall()
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns_out)
        writer.writerows(all_rows)
    conn.close()

    print(f"Registros recibidos de la fuente: {len(rows)}")
    print(f"Candidatos nuevos/actualizados: {saved}")
    print(f"Candidatos acumulados en CSV: {len(all_rows)}")
    print(f"CSV: {output}")
    print("Estos son candidatos de registro mercantil; todavía no son oportunidades comerciales verificadas.")


if __name__ == "__main__":
    main()
