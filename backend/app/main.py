import math
import random
import statistics
import uuid
from typing import Any

import httpx
import psycopg
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.llm_advisor import LLM_MODEL, _clean_qwen_response, advise_customer
from app.seed_vision import seed_catalog_images, seed_embeddings
from app.storage import UPLOADS_BUCKET, get_presigned_url, get_public_url, upload_bytes
from app.vision_search import DATABASE_URL, FALLBACK_MODE, OLLAMA_BASE_URL, search_by_image

app = FastAPI(title="Mes Plantes AI POC API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatTurn(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)


class AdvisorRequest(BaseModel):
    question: str = Field(..., min_length=2, description="Question du client")
    limit: int = Field(default=4, ge=1, le=20)
    history: list[ChatTurn] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, str]:
    """Check API and PostgreSQL connectivity."""
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    return {"status": "ok"}


@app.get("/products")
def list_products() -> list[dict[str, Any]]:
    """Return all products from the catalog."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, sku, name, category, aisle, price, stock_qty,
                       exposure, pet_safe, image_key, tags,
                       (embedding IS NOT NULL) AS has_embedding,
                       discount_pct
                FROM products
                ORDER BY id
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "sku": row[1],
            "name": row[2],
            "category": row[3],
            "aisle": row[4],
            "price": float(row[5]),
            "stock_qty": row[6],
            "exposure": row[7],
            "pet_safe": row[8],
            "image_key": row[9],
            "image_url": get_public_url(row[9]) if row[9] else None,
            "tags": row[10] or [],
            "has_embedding": row[11],
            "discount_pct": row[12] or 0,
            "discounted_price": round(float(row[5]) * (1 - (row[12] or 0) / 100), 2),
        }
        for row in rows
    ]


AI_TRENDS: list[dict[str, Any]] = [
    {"topic": "Plantes sans danger pour animaux (pet-safe)", "count": 48, "sample_question": "Je cherche une plante d'intérieur sans danger pour mon chat."},
    {"topic": "Protection contre le gel & hivernage", "count": 37, "sample_question": "Comment protéger mon olivier du gel cet hiver ?"},
    {"topic": "Décoration de Noël & luminaires", "count": 31, "sample_question": "Qu'avez-vous pour décorer une terrasse le soir ?"},
    {"topic": "Plantes d'intérieur faciles", "count": 22, "sample_question": "Quelle plante d'intérieur pour une pièce mi-ombre ?"},
]


@app.post("/advisor")
async def advisor(payload: AdvisorRequest) -> dict[str, Any]:
    """Answer a customer question using RAG + qwen3:8b."""
    q_lower = payload.question.lower()
    if any(k in q_lower for k in ("chat", "chien", "animal", "animaux", "pet", "toxique")):
        AI_TRENDS[0]["count"] += 1
    elif any(k in q_lower for k in ("gel", "hiver", "froid", "voile", "olivier")):
        AI_TRENDS[1]["count"] += 1
    elif any(k in q_lower for k in ("noël", "noel", "sapin", "lanterne", "déco", "terrasse")):
        AI_TRENDS[2]["count"] += 1
    else:
        AI_TRENDS[3]["count"] += 1
    return await advise_customer(
        payload.question,
        limit=payload.limit,
        history=[turn.model_dump() for turn in payload.history],
    )


@app.post("/vision-search")
async def vision_search(
    file: UploadFile = File(...),
    limit: int = Query(default=5, ge=1, le=20),
    analysis_mode: str = Query(default="search", pattern="^(search|doctor)$"),
) -> dict[str, Any]:
    """Upload a client image to MinIO and identify or diagnose it via llava:7b + pgvector."""
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Fichier image vide.")

    ext = (file.filename or "upload.jpg").rsplit(".", 1)[-1].lower()
    upload_key = f"{uuid.uuid4().hex}.{ext}"
    upload_bytes(
        key=upload_key,
        data=image_bytes,
        bucket=UPLOADS_BUCKET,
        content_type=file.content_type or "image/jpeg",
    )

    result = await search_by_image(image_bytes, limit=limit, mode_type=analysis_mode)
    result["upload_key"] = upload_key
    result["upload_url"] = get_presigned_url(upload_key, bucket=UPLOADS_BUCKET)
    return result


@app.post("/seed-vision")
async def trigger_seed_vision(force: bool = Query(default=False)) -> dict[str, int]:
    """Upload catalog images (if present) and generate missing product embeddings."""
    uploaded = seed_catalog_images()
    updated = await seed_embeddings(force=force)
    return {"uploaded_images": uploaded, "updated_embeddings": updated}


HEATMAP_SLOTS: dict[str, list[dict[str, Any]]] = {
    "morning": [
        {"id": "entree", "label": "Entrée & Accueil", "aisle_prefix": None, "visitors": 18, "capacity": 50, "dwell_min": 3},
        {"id": "serre", "label": "Serre Chaude (A01–A15)", "aisle_prefix": "Serre Chaude", "visitors": 24, "capacity": 60, "dwell_min": 11},
        {"id": "exterieur", "label": "Allée Extérieure (B01–B20)", "aisle_prefix": "Allée Extérieure", "visitors": 31, "capacity": 70, "dwell_min": 14},
        {"id": "terreaux", "label": "Terreaux & Hiver (C01–C12)", "aisle_prefix": "Allée C", "visitors": 19, "capacity": 45, "dwell_min": 7},
        {"id": "deco", "label": "Hall Déco & Noël (D01–D12)", "aisle_prefix": "Hall Déco", "visitors": 28, "capacity": 65, "dwell_min": 13},
        {"id": "caisses", "label": "Caisses", "aisle_prefix": None, "visitors": 12, "capacity": 40, "dwell_min": 4},
    ],
    "afternoon": [
        {"id": "entree", "label": "Entrée & Accueil", "aisle_prefix": None, "visitors": 34, "capacity": 50, "dwell_min": 4},
        {"id": "serre", "label": "Serre Chaude (A01–A15)", "aisle_prefix": "Serre Chaude", "visitors": 49, "capacity": 60, "dwell_min": 16},
        {"id": "exterieur", "label": "Allée Extérieure (B01–B20)", "aisle_prefix": "Allée Extérieure", "visitors": 38, "capacity": 70, "dwell_min": 13},
        {"id": "terreaux", "label": "Terreaux & Hiver (C01–C12)", "aisle_prefix": "Allée C", "visitors": 27, "capacity": 45, "dwell_min": 8},
        {"id": "deco", "label": "Hall Déco & Noël (D01–D12)", "aisle_prefix": "Hall Déco", "visitors": 58, "capacity": 65, "dwell_min": 19},
        {"id": "caisses", "label": "Caisses", "aisle_prefix": None, "visitors": 29, "capacity": 40, "dwell_min": 6},
    ],
    "weekend": [
        {"id": "entree", "label": "Entrée & Accueil", "aisle_prefix": None, "visitors": 46, "capacity": 50, "dwell_min": 5},
        {"id": "serre", "label": "Serre Chaude (A01–A15)", "aisle_prefix": "Serre Chaude", "visitors": 57, "capacity": 60, "dwell_min": 21},
        {"id": "exterieur", "label": "Allée Extérieure (B01–B20)", "aisle_prefix": "Allée Extérieure", "visitors": 61, "capacity": 70, "dwell_min": 17},
        {"id": "terreaux", "label": "Terreaux & Hiver (C01–C12)", "aisle_prefix": "Allée C", "visitors": 39, "capacity": 45, "dwell_min": 10},
        {"id": "deco", "label": "Hall Déco & Noël (D01–D12)", "aisle_prefix": "Hall Déco", "visitors": 63, "capacity": 65, "dwell_min": 24},
        {"id": "caisses", "label": "Caisses", "aisle_prefix": None, "visitors": 37, "capacity": 40, "dwell_min": 9},
    ],
}


@app.get("/store-heatmap")
def store_heatmap(slot: str = Query(default="afternoon")) -> dict[str, Any]:
    """Return mock store foot-traffic heatmap enriched with real catalog products per zone."""
    zones_template = HEATMAP_SLOTS.get(slot, HEATMAP_SLOTS["afternoon"])

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sku, name, aisle, price, stock_qty FROM products ORDER BY id")
            all_products = cur.fetchall()

    zones = []
    total_visitors = 0
    for z in zones_template:
        prefix = z["aisle_prefix"]
        zone_products = [
            {"sku": r[0], "name": r[1], "aisle": r[2], "price": float(r[3]), "stock_qty": r[4]}
            for r in all_products
            if prefix and r[2].startswith(prefix)
        ]
        occupancy = round((z["visitors"] / z["capacity"]) * 100)
        total_visitors += z["visitors"]
        zones.append({
            "id": z["id"],
            "label": z["label"],
            "visitors": z["visitors"],
            "capacity": z["capacity"],
            "occupancy_pct": occupancy,
            "dwell_min": z["dwell_min"],
            "products": zone_products,
        })

    hottest = max(zones, key=lambda item: item["occupancy_pct"])
    avg_dwell = round(sum(z["dwell_min"] for z in zones) / len(zones), 1)
    return {
        "slot": slot,
        "total_visitors": total_visitors,
        "hottest_zone": hottest["label"],
        "avg_dwell_min": avg_dwell,
        "zones": zones,
    }


@app.get("/stock-alerts")
def stock_alerts(threshold: int = Query(default=25, ge=1, le=200)) -> list[dict[str, Any]]:
    """Return low-stock products crossed with afternoon store zone traffic."""
    afternoon_zones = HEATMAP_SLOTS["afternoon"]
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sku, name, category, aisle, price, stock_qty
                FROM products
                WHERE stock_qty <= %s
                ORDER BY stock_qty ASC, id ASC
                """,
                (threshold,),
            )
            rows = cur.fetchall()

    alerts = []
    for sku, name, category, aisle, price, stock_qty in rows:
        zone_occupancy = 50
        zone_label = aisle
        for z in afternoon_zones:
            if z["aisle_prefix"] and aisle.startswith(z["aisle_prefix"]):
                zone_occupancy = round((z["visitors"] / z["capacity"]) * 100)
                zone_label = z["label"]
                break

        if stock_qty <= 10 or (stock_qty <= 15 and zone_occupancy >= 80):
            severity = "critique"
        elif stock_qty <= 20:
            severity = "attention"
        else:
            severity = "surveiller"

        alerts.append({
            "sku": sku,
            "name": name,
            "category": category,
            "aisle": aisle,
            "price": float(price),
            "stock_qty": stock_qty,
            "zone_label": zone_label,
            "zone_occupancy_pct": zone_occupancy,
            "severity": severity,
        })
    return alerts


@app.get("/ai-trends")
def ai_trends() -> list[dict[str, Any]]:
    """Return top customer AI topics sorted by query count."""
    return sorted(AI_TRENDS, key=lambda item: item["count"], reverse=True)


SCENARIO_CONFIG: dict[str, dict[str, Any]] = {
    "normal": {
        "label": "Semaine standard",
        "traffic_factor": 1.0,
        "category_multipliers": {},
    },
    "frost": {
        "label": "Vague de gel annoncée (J+2)",
        "traffic_factor": 0.9,
        "category_multipliers": {
            "Protection Hiver": 2.4,
            "Univers Saison / Noël": 1.4,
            "Plantes d'Intérieur": 1.2,
            "Plantes Vivaces": 0.7,
        },
    },
    "sunny_weekend": {
        "label": "Week-end ensoleillé",
        "traffic_factor": 1.45,
        "category_multipliers": {
            "Plantes Vivaces": 1.8,
            "Arbres Fruitiers & Agrumes": 1.7,
            "Terreaux & Engrais": 1.6,
            "Décoration & Luminaire": 1.5,
        },
    },
}

BASE_DAILY_ROTATION: dict[str, float] = {
    "FAM-PL-001": 3.2,
    "FAM-PL-002": 1.8,
    "FAM-PL-003": 2.1,
    "FAM-PL-004": 1.5,
    "FAM-PL-005": 1.4,
    "FAM-PL-006": 2.3,
    "FAM-PL-007": 3.0,
    "FAM-PL-008": 1.6,
    "FAM-PL-009": 2.8,
    "FAM-PL-010": 2.2,
    "FAM-SO-010": 8.5,
    "FAM-SO-011": 4.2,
    "FAM-SO-012": 3.5,
    "FAM-HI-020": 4.0,
    "FAM-HI-021": 2.5,
    "FAM-DE-045": 2.4,
    "FAM-DE-046": 2.7,
    "FAM-DE-047": 2.1,
    "FAM-NO-088": 2.0,
    "FAM-NO-089": 1.9,
}


def _compute_predictions(scenario: str) -> dict[str, Any]:
    cfg = SCENARIO_CONFIG.get(scenario, SCENARIO_CONFIG["normal"])
    cat_mults = cfg["category_multipliers"]

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sku, name, category, aisle, stock_qty, price FROM products ORDER BY id"
            )
            rows = cur.fetchall()

    items = []
    for sku, name, category, aisle, stock_qty, price in rows:
        base_rate = BASE_DAILY_ROTATION.get(sku, 2.0)
        mult = cat_mults.get(category, 1.0)
        daily_sales = round(base_rate * mult, 1)
        days_left = round(stock_qty / daily_sales, 1) if daily_sales > 0 else 99.0
        reorder_qty = max(0, round(daily_sales * 14 - stock_qty))

        if days_left <= 4:
            risk = "critique"
        elif days_left <= 8:
            risk = "attention"
        else:
            risk = "ok"

        items.append({
            "sku": sku,
            "name": name,
            "category": category,
            "aisle": aisle,
            "stock_qty": stock_qty,
            "price": float(price),
            "predicted_daily_sales": daily_sales,
            "days_until_stockout": days_left,
            "recommended_reorder": reorder_qty,
            "risk": risk,
        })

    items.sort(key=lambda x: x["days_until_stockout"])

    base_days = [
        ("Lun", 190),
        ("Mar", 210),
        ("Mer", 260),
        ("Jeu", 240),
        ("Ven", 310),
        ("Sam", 520),
        ("Dim", 460),
    ]
    forecast_7d = [
        {
            "day": day,
            "predicted_visitors": round(val * cfg["traffic_factor"]),
            "confidence_pct": 88 if i < 3 else 79,
        }
        for i, (day, val) in enumerate(base_days)
    ]

    cross_sell = [
        {"trigger": "Olivier d'Europe (FAM-PL-004)", "recommended": "Terreau Universel 40L + Voile d'Hivernage", "probability_pct": 74},
        {"trigger": "Lavande Anglaise (FAM-PL-001)", "recommended": "Terreau Universel Horticole 40L", "probability_pct": 68},
        {"trigger": "Sapin Nordmann (FAM-NO-088)", "recommended": "Lanterne Extérieure Solaire", "probability_pct": 52},
    ]

    return {
        "scenario": scenario,
        "scenario_label": cfg["label"],
        "stock_forecasts": items,
        "forecast_7d": forecast_7d,
        "cross_sell": cross_sell,
    }


@app.get("/predictions")
def predictions(scenario: str = Query(default="normal")) -> dict[str, Any]:
    """Return mock stockout predictions, 7-day traffic forecast, and cross-sell probabilities."""
    return _compute_predictions(scenario)


@app.post("/predictions/ai-brief")
async def predictions_ai_brief(scenario: str = Query(default="normal")) -> dict[str, str]:
    """Generate a short manager action brief from predictions using qwen3:8b."""
    data = _compute_predictions(scenario)
    top_risks = [
        f"- {p['name']} ({p['sku']}): stock {p['stock_qty']}, rupture estimée dans {p['days_until_stockout']}j, réappro conseillé: +{p['recommended_reorder']}"
        for p in data["stock_forecasts"][:4]
    ]
    prompt = (
        f"Scénario magasin : {data['scenario_label']}.\n"
        f"Produits les plus exposés à la rupture :\n" + "\n".join(top_risks) + "\n\n"
        "En tant qu'assistant directeur de magasin Mes Plantes, donne 3 actions concrètes et courtes en français."
    )
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            brief = _clean_qwen_response(resp.json()["message"]["content"])
            return {"mode": "ollama", "brief": brief}
    except Exception:
        if not FALLBACK_MODE:
            raise
        first = data["stock_forecasts"][0]
        return {
            "mode": "fallback",
            "brief": (
                f"1. Priorité réappro : commander +{first['recommended_reorder']} unités de {first['name']} "
                f"(rupture estimée sous {first['days_until_stockout']} jours).\n"
                f"2. Adapter la mise en avant au scénario « {data['scenario_label']} ».\n"
                "3. Proposer le cross-sell Terreau Universel 40L en caisse et rayon extérieur."
            ),
        }


class MovementRequest(BaseModel):
    sku: str = Field(..., min_length=2)
    movement_type: str = Field(..., pattern="^(purchase|sale)$")
    qty: int = Field(..., ge=1, le=5000)
    note: str = Field(default="")


@app.get("/inventory-overview")
def inventory_overview() -> dict[str, Any]:
    """Return purchases, sales, stock levels per product and per category, plus recent movements."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.sku,
                    p.name,
                    p.category,
                    p.aisle,
                    p.price,
                    p.stock_qty,
                    COALESCE(SUM(CASE WHEN m.movement_type = 'purchase' THEN m.qty ELSE 0 END), 0) AS purchases_qty,
                    COALESCE(SUM(CASE WHEN m.movement_type = 'sale' THEN m.qty ELSE 0 END), 0) AS sales_qty,
                    COALESCE(SUM(CASE WHEN m.movement_type = 'sale' THEN m.qty * m.unit_price ELSE 0 END), 0) AS sales_revenue
                FROM products p
                LEFT JOIN stock_movements m ON m.sku = p.sku
                GROUP BY p.id, p.sku, p.name, p.category, p.aisle, p.price, p.stock_qty
                ORDER BY p.id
                """
            )
            prod_rows = cur.fetchall()

            cur.execute(
                """
                SELECT m.id, m.sku, p.name, m.movement_type, m.qty, m.unit_price, m.note, m.created_at
                FROM stock_movements m
                LEFT JOIN products p ON p.sku = m.sku
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT 15
                """
            )
            mov_rows = cur.fetchall()

    products = []
    by_category: dict[str, dict[str, Any]] = {}
    total_stock = 0
    stock_value = 0.0
    total_purchases = 0
    total_sales = 0
    total_revenue = 0.0

    for sku, name, category, aisle, price, stock_qty, purchases_qty, sales_qty, sales_rev in prod_rows:
        p_val = float(price)
        val = round(stock_qty * p_val, 2)
        total_stock += stock_qty
        stock_value += val
        total_purchases += int(purchases_qty)
        total_sales += int(sales_qty)
        total_revenue += float(sales_rev)

        products.append({
            "sku": sku,
            "name": name,
            "category": category,
            "aisle": aisle,
            "price": p_val,
            "stock_qty": stock_qty,
            "purchases_qty": int(purchases_qty),
            "sales_qty": int(sales_qty),
            "stock_value": val,
        })

        cat_entry = by_category.setdefault(
            category,
            {"category": category, "purchases_qty": 0, "sales_qty": 0, "stock_qty": 0},
        )
        cat_entry["purchases_qty"] += int(purchases_qty)
        cat_entry["sales_qty"] += int(sales_qty)
        cat_entry["stock_qty"] += stock_qty

    recent_movements = [
        {
            "id": r[0],
            "sku": r[1],
            "product_name": r[2] or r[1],
            "movement_type": r[3],
            "qty": r[4],
            "unit_price": float(r[5]),
            "note": r[6] or "",
            "created_at": r[7].isoformat() if r[7] else "",
        }
        for r in mov_rows
    ]

    return {
        "kpis": {
            "total_stock_units": total_stock,
            "stock_value_eur": round(stock_value, 2),
            "total_purchases_qty": total_purchases,
            "total_sales_qty": total_sales,
            "sales_revenue_eur": round(total_revenue, 2),
        },
        "chart_by_category": list(by_category.values()),
        "products": products,
        "recent_movements": recent_movements,
    }


@app.post("/inventory-movement")
def record_inventory_movement(payload: MovementRequest) -> dict[str, Any]:
    """Record a purchase (+stock) or sale (-stock) and return the updated stock."""
    sku = payload.sku.upper()
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT stock_qty, price FROM products WHERE sku = %s", (sku,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Produit introuvable : {sku}")

            current_stock, price = row[0], float(row[1])
            if payload.movement_type == "sale" and payload.qty > current_stock:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stock insuffisant pour {sku} (disponible : {current_stock}).",
                )

            delta = payload.qty if payload.movement_type == "purchase" else -payload.qty
            cur.execute(
                "UPDATE products SET stock_qty = stock_qty + %s WHERE sku = %s RETURNING stock_qty",
                (delta, sku),
            )
            new_stock = cur.fetchone()[0]

            unit_price = round(price * 0.55, 2) if payload.movement_type == "purchase" else price
            note = payload.note.strip() or (
                "Réapprovisionnement UI" if payload.movement_type == "purchase" else "Vente UI"
            )
            cur.execute(
                """
                INSERT INTO stock_movements (sku, movement_type, qty, unit_price, note)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (sku, payload.movement_type, payload.qty, unit_price, note),
            )
        conn.commit()
    return {"sku": sku, "movement_type": payload.movement_type, "qty": payload.qty, "new_stock": new_stock}


@app.post("/inventory-auto-simulate")
def inventory_auto_simulate(
    mode: str = Query(default="purchases", pattern="^(purchases|cycle)$"),
    threshold: int = Query(default=18, ge=1, le=100),
) -> dict[str, Any]:
    """Automatically simulate supplier purchases for low-stock items (and optional random sales cycle)."""
    actions: list[dict[str, Any]] = []
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            if mode == "cycle":
                cur.execute(
                    "SELECT sku, stock_qty, price FROM products WHERE stock_qty > 0 ORDER BY RANDOM() LIMIT 3"
                )
                for sku, stock_qty, price in cur.fetchall():
                    sold = min(stock_qty, random.randint(1, 5))
                    cur.execute(
                        "UPDATE products SET stock_qty = stock_qty - %s WHERE sku = %s",
                        (sold, sku),
                    )
                    cur.execute(
                        "INSERT INTO stock_movements (sku, movement_type, qty, unit_price, note) VALUES (%s, 'sale', %s, %s, %s)",
                        (sku, sold, float(price), "Vente auto (simulation)"),
                    )
                    actions.append({"sku": sku, "movement_type": "sale", "qty": sold})

            cur.execute(
                "SELECT sku, stock_qty, price FROM products WHERE stock_qty <= %s ORDER BY stock_qty ASC",
                (threshold,),
            )
            low_items = cur.fetchall()
            if not low_items and mode == "purchases":
                # Si aucun produit n'est sous le seuil, réapprovisionner 2 produits aléatoires pour la démo
                cur.execute("SELECT sku, stock_qty, price FROM products ORDER BY stock_qty ASC LIMIT 2")
                low_items = cur.fetchall()

            for sku, stock_qty, price in low_items:
                buy_qty = max(12, 35 - stock_qty)
                cur.execute(
                    "UPDATE products SET stock_qty = stock_qty + %s WHERE sku = %s",
                    (buy_qty, sku),
                )
                cur.execute(
                    "INSERT INTO stock_movements (sku, movement_type, qty, unit_price, note) VALUES (%s, 'purchase', %s, %s, %s)",
                    (sku, buy_qty, round(float(price) * 0.55, 2), "Achat auto (réappro seuil)"),
                )
                actions.append({"sku": sku, "movement_type": "purchase", "qty": buy_qty})
        conn.commit()

    return {"mode": mode, "actions_count": len(actions), "actions": actions}


class SensorIngestRequest(BaseModel):
    sensor_id: str = Field(..., min_length=2)
    soil_moisture_pct: float = Field(..., ge=0, le=100)
    air_humidity_pct: float = Field(..., ge=0, le=100)
    temperature_c: float = Field(..., ge=-20, le=60)


@app.get("/sensors")
def list_sensors() -> dict[str, Any]:
    """Return latest IoT soil/air humidity and temperature readings per zone."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sensor_id, zone_label, aisle_prefix, soil_moisture_pct,
                       air_humidity_pct, temperature_c, target_min_moisture, updated_at
                FROM sensor_readings
                ORDER BY id
                """
            )
            sensor_rows = cur.fetchall()
            cur.execute("SELECT sku, name, aisle FROM products ORDER BY id")
            prod_rows = cur.fetchall()

    sensors = []
    needs_watering_count = 0
    for sid, label, prefix, soil, air, temp, target_min, updated_at in sensor_rows:
        soil_f = float(soil)
        air_f = float(air)
        temp_f = float(temp)
        target_f = float(target_min)
        needs_watering = soil_f < target_f
        if needs_watering:
            needs_watering_count += 1
            status = "sec"
        elif soil_f > 80.0:
            status = "saturé"
        else:
            status = "optimal"

        plants = [
            {"sku": r[0], "name": r[1], "aisle": r[2]}
            for r in prod_rows
            if r[2].startswith(prefix)
        ]
        sensors.append({
            "sensor_id": sid,
            "zone_label": label,
            "soil_moisture_pct": soil_f,
            "air_humidity_pct": air_f,
            "temperature_c": temp_f,
            "target_min_moisture": target_f,
            "status": status,
            "needs_watering": needs_watering,
            "plants": plants,
            "updated_at": updated_at.isoformat() if updated_at else "",
        })

    avg_soil = round(sum(s["soil_moisture_pct"] for s in sensors) / len(sensors), 1) if sensors else 0
    avg_temp = round(sum(s["temperature_c"] for s in sensors) / len(sensors), 1) if sensors else 0
    return {
        "kpis": {
            "total_sensors": len(sensors),
            "needs_watering": needs_watering_count,
            "avg_soil_moisture": avg_soil,
            "avg_temperature_c": avg_temp,
        },
        "sensors": sensors,
    }


@app.post("/sensors/ingest")
def ingest_sensor(payload: SensorIngestRequest) -> dict[str, Any]:
    """Ingest a real or simulated IoT sensor reading."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sensor_readings
                SET soil_moisture_pct = %s,
                    air_humidity_pct = %s,
                    temperature_c = %s,
                    updated_at = NOW()
                WHERE sensor_id = %s
                RETURNING sensor_id
                """,
                (payload.soil_moisture_pct, payload.air_humidity_pct, payload.temperature_c, payload.sensor_id),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail=f"Capteur inconnu : {payload.sensor_id}")
    return {"status": "updated", "sensor_id": payload.sensor_id}


@app.post("/sensors/water/{sensor_id}")
def water_zone(sensor_id: str) -> dict[str, Any]:
    """Trigger zone irrigation and restore optimal soil moisture."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sensor_readings
                SET soil_moisture_pct = LEAST(75.0, target_min_moisture + 22.0),
                    air_humidity_pct = LEAST(85.0, air_humidity_pct + 4.0),
                    updated_at = NOW()
                WHERE sensor_id = %s
                RETURNING sensor_id, soil_moisture_pct
                """,
                (sensor_id,),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail=f"Capteur inconnu : {sensor_id}")
    return {"sensor_id": row[0], "soil_moisture_pct": float(row[1])}


@app.post("/sensors/simulate-tick")
def simulate_sensor_tick() -> dict[str, str]:
    """Simulate natural soil drying and microclimate drift across all sensors."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sensor_id, soil_moisture_pct, air_humidity_pct, temperature_c FROM sensor_readings")
            for sid, soil, air, temp in cur.fetchall():
                new_soil = max(12.0, round(float(soil) - random.uniform(2.5, 6.5), 1))
                new_air = min(92.0, max(30.0, round(float(air) + random.uniform(-2.0, 2.0), 1)))
                new_temp = round(float(temp) + random.uniform(-0.4, 0.5), 1)
                cur.execute(
                    """
                    UPDATE sensor_readings
                    SET soil_moisture_pct = %s, air_humidity_pct = %s, temperature_c = %s, updated_at = NOW()
                    WHERE sensor_id = %s
                    """,
                    (new_soil, new_air, new_temp, sid),
                )
        conn.commit()
    return {"status": "tick_applied"}


PLANT_CATEGORIES = {
    "Plantes d'Intérieur",
    "Plantes Vivaces",
    "Arbres Fruitiers & Agrumes",
}


class MarkdownRequest(BaseModel):
    sku: str = Field(..., min_length=2)
    discount_pct: int = Field(..., ge=0, le=70)


@app.get("/waste-markdowns")
def waste_markdowns() -> dict[str, Any]:
    """Identify slow-moving or stressed plants and recommend weekend flash discounts."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sku, name, category, aisle, price, stock_qty, discount_pct
                FROM products
                ORDER BY id
                """
            )
            prod_rows = cur.fetchall()
            cur.execute(
                "SELECT aisle_prefix, soil_moisture_pct, target_min_moisture FROM sensor_readings"
            )
            sensor_rows = cur.fetchall()

    candidates = []
    active_promos = 0
    saved_value = 0.0

    for sku, name, category, aisle, price, stock_qty, discount_pct in prod_rows:
        if category not in PLANT_CATEGORIES or stock_qty <= 0:
            continue

        daily_rate = BASE_DAILY_ROTATION.get(sku, 2.0)
        days_of_stock = round(stock_qty / daily_rate, 1) if daily_rate > 0 else 99.0

        is_dry = False
        for prefix, soil, target_min in sensor_rows:
            if aisle.startswith(prefix) and float(soil) < float(target_min):
                is_dry = True
                break

        if days_of_stock < 8.5 and not is_dry and discount_pct == 0:
            continue

        reasons = []
        if days_of_stock >= 8.5:
            reasons.append(f"Rotation lente ({days_of_stock}j de stock)")
        if is_dry:
            reasons.append("Zone sèche (stress hydrique capteur)")

        suggested_pct = 30 if (days_of_stock >= 11 or (is_dry and days_of_stock >= 7)) else 20
        p_val = float(price)
        promo_price = round(p_val * (1 - suggested_pct / 100), 2)
        current_discount = int(discount_pct or 0)
        if current_discount > 0:
            active_promos += 1
        saved_value += round(stock_qty * promo_price * 0.4, 2)

        candidates.append({
            "sku": sku,
            "name": name,
            "category": category,
            "aisle": aisle,
            "stock_qty": stock_qty,
            "days_of_stock": days_of_stock,
            "is_dry": is_dry,
            "reasons": reasons or ["Surstock saisonnier"],
            "original_price": p_val,
            "suggested_discount_pct": suggested_pct,
            "suggested_promo_price": promo_price,
            "active_discount_pct": current_discount,
        })

    return {
        "kpis": {
            "at_risk_count": len(candidates),
            "active_promos": active_promos,
            "potential_saved_eur": round(saved_value, 2),
        },
        "candidates": candidates,
    }


@app.post("/waste-markdowns/apply")
def apply_waste_markdown(payload: MarkdownRequest) -> dict[str, Any]:
    """Activate or clear a weekend flash discount on a product."""
    sku = payload.sku.upper()
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE products SET discount_pct = %s WHERE sku = %s RETURNING sku, discount_pct",
                (payload.discount_pct, sku),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail=f"Produit introuvable : {sku}")
    return {"sku": row[0], "discount_pct": row[1]}


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return 0.0
    return round(num / (den_x * den_y), 3)


def _descriptive_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "stdev": 0.0, "q1": 0.0, "q3": 0.0, "iqr": 0.0}
    sorted_vals = sorted(values)
    qs = statistics.quantiles(sorted_vals, n=4) if len(sorted_vals) >= 4 else [sorted_vals[0], statistics.median(sorted_vals), sorted_vals[-1]]
    return {
        "mean": round(statistics.mean(sorted_vals), 2),
        "median": round(statistics.median(sorted_vals), 2),
        "stdev": round(statistics.stdev(sorted_vals), 2) if len(sorted_vals) > 1 else 0.0,
        "q1": round(qs[0], 2),
        "q3": round(qs[2], 2),
        "iqr": round(qs[2] - qs[0], 2),
    }


@app.get("/monitoring/stats")
def monitoring_stats() -> dict[str, Any]:
    """Compute descriptive stats, Pareto ABC, safety stock, Pearson correlation, anomalies, and 95% CI."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.sku,
                    p.name,
                    p.category,
                    p.aisle,
                    p.price,
                    p.stock_qty,
                    COALESCE(SUM(CASE WHEN m.movement_type = 'sale' THEN m.qty ELSE 0 END), 0) AS sales_qty,
                    COALESCE(SUM(CASE WHEN m.movement_type = 'sale' THEN m.qty * m.unit_price ELSE 0 END), 0) AS sales_rev
                FROM products p
                LEFT JOIN stock_movements m ON m.sku = p.sku
                GROUP BY p.id, p.sku, p.name, p.category, p.aisle, p.price, p.stock_qty
                ORDER BY p.id
                """
            )
            prod_rows = cur.fetchall()

            cur.execute(
                "SELECT sensor_id, zone_label, soil_moisture_pct, temperature_c FROM sensor_readings ORDER BY id"
            )
            sensor_rows = cur.fetchall()

    stocks = [float(r[5]) for r in prod_rows]
    sales_qtys = [float(r[6]) for r in prod_rows]
    revenues = [float(r[7]) for r in prod_rows]

    # 1. Pareto ABC + 2. Safety Stock (Z=1.65 for 95% service level, lead_time L=3 days)
    total_rev = sum(revenues) or 1.0
    sorted_for_abc = sorted(prod_rows, key=lambda r: float(r[7]), reverse=True)
    cum_rev = 0.0
    abc_items = []
    abc_counts = {"A": 0, "B": 0, "C": 0}
    z_service = 1.65
    lead_time_days = 3

    for sku, name, category, aisle, price, stock_qty, sales_qty, sales_rev in sorted_for_abc:
        rev = float(sales_rev)
        cum_rev += rev
        cum_pct = round((cum_rev / total_rev) * 100, 1)
        if cum_pct <= 80.0 or abc_counts["A"] == 0:
            abc_class = "A"
        elif cum_pct <= 95.0:
            abc_class = "B"
        else:
            abc_class = "C"
        abc_counts[abc_class] += 1

        daily_mean = BASE_DAILY_ROTATION.get(sku, max(1.0, float(sales_qty) / 7.0))
        sigma_d = round(daily_mean * 0.35, 2)
        safety_stock = math.ceil(z_service * sigma_d * math.sqrt(lead_time_days))
        reorder_point = math.ceil(daily_mean * lead_time_days + safety_stock)

        abc_items.append({
            "sku": sku,
            "name": name,
            "abc_class": abc_class,
            "sales_qty": int(sales_qty),
            "sales_revenue": round(rev, 2),
            "cum_revenue_pct": cum_pct,
            "stock_qty": int(stock_qty),
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "below_rop": int(stock_qty) <= reorder_point,
        })

    # 3. Corrélation de Pearson entre affluence zone (après-midi) et ventes des produits de la zone
    zone_traffic: list[float] = []
    zone_sales: list[float] = []
    zone_pairs = []
    for z in HEATMAP_SLOTS["afternoon"]:
        prefix = z["aisle_prefix"]
        if not prefix:
            continue
        z_sales = sum(int(r[6]) for r in prod_rows if r[3].startswith(prefix))
        zone_traffic.append(float(z["visitors"]))
        zone_sales.append(float(z_sales))
        zone_pairs.append({
            "zone": z["label"],
            "visitors": z["visitors"],
            "sales_qty": z_sales,
        })
    pearson_r = _pearson_r(zone_traffic, zone_sales)

    # 4. Détection d'anomalies (Z-score sur stocks et humidité sol)
    anomalies = []
    if len(stocks) > 1:
        m_stock, s_stock = statistics.mean(stocks), statistics.stdev(stocks)
        if s_stock > 0:
            for r in prod_rows:
                z_val = (float(r[5]) - m_stock) / s_stock
                if abs(z_val) >= 1.5:
                    anomalies.append({
                        "source": f"Stock · {r[0]}",
                        "label": r[1],
                        "value": f"{r[5]} unités",
                        "z_score": round(z_val, 2),
                        "direction": "pic" if z_val > 0 else "creux",
                    })

    if len(sensor_rows) > 1:
        soils = [float(s[2]) for s in sensor_rows]
        m_soil, s_soil = statistics.mean(soils), statistics.stdev(soils)
        if s_soil > 0:
            for sid, label, soil, temp in sensor_rows:
                z_val = (float(soil) - m_soil) / s_soil
                if abs(z_val) >= 1.1:
                    anomalies.append({
                        "source": f"Capteur · {sid}",
                        "label": label,
                        "value": f"{float(soil):.1f}% sol / {float(temp):.1f}°C",
                        "z_score": round(z_val, 2),
                        "direction": "élevé" if z_val > 0 else "bas",
                    })

    # 5. Prévision 7j avec intervalle de confiance à 95%
    base_days = [("Lun", 190), ("Mar", 210), ("Mer", 260), ("Jeu", 240), ("Ven", 310), ("Sam", 520), ("Dim", 460)]
    forecast_ci = []
    for i, (day, val) in enumerate(base_days):
        margin = round(val * (0.08 + i * 0.015))
        forecast_ci.append({
            "day": day,
            "mean": val,
            "ci_low": val - margin,
            "ci_high": val + margin,
        })

    return {
        "descriptive": {
            "stock": _descriptive_stats(stocks),
            "sales": _descriptive_stats(sales_qtys),
        },
        "abc_summary": abc_counts,
        "abc_items": abc_items,
        "correlation": {
            "pearson_r": pearson_r,
            "pairs": zone_pairs,
        },
        "anomalies": anomalies,
        "forecast_ci": forecast_ci,
    }


AUTOMATION_RULES: list[dict[str, str]] = [
    {
        "id": "auto_rop_reorder",
        "name": "Réapprovisionnement automatique (ROP)",
        "trigger": "Seuil : stock_qty <= Point de commande",
        "description": "Génère des achats fournisseurs automatiques pour tout produit passé sous son point de commande statistique.",
    },
    {
        "id": "auto_iot_irrigation",
        "name": "Arrosage IoT automatique",
        "trigger": "Capteur : humidité sol < seuil cible",
        "description": "Déclenche l'irrigation automatique des zones détectées comme sèches par les capteurs serre.",
    },
    {
        "id": "auto_waste_flash",
        "name": "Démarque Flash Anti-Gaspillage",
        "trigger": "Planifié : Vendredi 08:00",
        "description": "Active automatiquement les remises (-20% / -30%) sur les plantes à rotation lente ou en zone sèche.",
    },
    {
        "id": "auto_embed_sync",
        "name": "Synchronisation Embeddings IA",
        "trigger": "Événement : Nouveau produit / image",
        "description": "Calcule les vecteurs nomic-embed-text (768D) pour tout produit sans embedding dans pgvector.",
    },
    {
        "id": "auto_morning_brief",
        "name": "Briefing matinal IA (qwen3:8b)",
        "trigger": "Planifié : Tous les jours 07:30",
        "description": "Génère une synthèse managériale combinant ruptures ROP, capteurs secs et anomalies Z-score.",
    },
]


def _log_automation(rule_id: str, rule_name: str, summary: str, trigger_type: str = "manual") -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO automation_logs (rule_id, rule_name, trigger_type, status, summary)
                VALUES (%s, %s, %s, 'success', %s)
                """,
                (rule_id, rule_name, trigger_type, summary),
            )
        conn.commit()


async def _execute_rule(rule_id: str, trigger_type: str = "manual") -> dict[str, Any]:
    rule = next((r for r in AUTOMATION_RULES if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Règle inconnue : {rule_id}")

    if rule_id == "auto_rop_reorder":
        stats = monitoring_stats()
        below = [item for item in stats["abc_items"] if item["below_rop"]]
        reordered = []
        if below:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    for item in below:
                        buy_qty = max(10, item["reorder_point"] * 2 - item["stock_qty"])
                        cur.execute("SELECT price FROM products WHERE sku = %s", (item["sku"],))
                        price = float(cur.fetchone()[0])
                        cur.execute(
                            "UPDATE products SET stock_qty = stock_qty + %s WHERE sku = %s",
                            (buy_qty, item["sku"]),
                        )
                        cur.execute(
                            "INSERT INTO stock_movements (sku, movement_type, qty, unit_price, note) VALUES (%s, 'purchase', %s, %s, %s)",
                            (item["sku"], buy_qty, round(price * 0.55, 2), "Auto ROP"),
                        )
                        reordered.append(f"+{buy_qty} {item['sku']}")
                conn.commit()
        summary = f"Commandes générées : {', '.join(reordered)}" if reordered else "Aucun produit sous son point de commande."

    elif rule_id == "auto_iot_irrigation":
        sensor_data = list_sensors()
        dry = [s for s in sensor_data["sensors"] if s["needs_watering"]]
        watered = []
        for s in dry:
            water_zone(s["sensor_id"])
            watered.append(s["sensor_id"])
        summary = f"Zones arrosées : {', '.join(watered)}" if watered else "Toutes les zones ont une humidité optimale."

    elif rule_id == "auto_waste_flash":
        waste = waste_markdowns()
        applied = []
        for c in waste["candidates"]:
            if c["active_discount_pct"] == 0:
                apply_waste_markdown(MarkdownRequest(sku=c["sku"], discount_pct=c["suggested_discount_pct"]))
                applied.append(f"{c['sku']} (-{c['suggested_discount_pct']}%)")
        summary = f"Promos activées : {', '.join(applied)}" if applied else "Aucune nouvelle promo flash à activer."

    elif rule_id == "auto_embed_sync":
        updated = await seed_embeddings(force=False)
        summary = f"{updated} nouvel(s) embedding(s) généré(s) via nomic-embed-text."

    else:  # auto_morning_brief
        stats = monitoring_stats()
        sensors = list_sensors()
        below_count = sum(1 for i in stats["abc_items"] if i["below_rop"])
        prompt = (
            f"Briefing matin Mes Plantes : {below_count} produit(s) sous point de commande, "
            f"{sensors['kpis']['needs_watering']} zone(s) sèche(s), "
            f"{len(stats['anomalies'])} anomalie(s) Z-score. "
            "Résume en 3 phrases courtes les priorités du matin pour l'équipe."
        )
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False},
                )
                resp.raise_for_status()
                summary = _clean_qwen_response(resp.json()["message"]["content"])
        except Exception:
            if not FALLBACK_MODE:
                raise
            summary = (
                f"Priorités matin : {below_count} réappro(s) ROP, "
                f"{sensors['kpis']['needs_watering']} zone(s) à arroser, "
                f"{len(stats['anomalies'])} anomalie(s) à vérifier."
            )

    _log_automation(rule["id"], rule["name"], summary, trigger_type=trigger_type)
    return {"rule_id": rule["id"], "rule_name": rule["name"], "summary": summary}


@app.get("/automations")
def get_automations() -> dict[str, Any]:
    """Return automation rules and recent execution logs."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, rule_id, rule_name, trigger_type, status, summary, executed_at
                FROM automation_logs
                ORDER BY id DESC
                LIMIT 20
                """
            )
            rows = cur.fetchall()

    logs = [
        {
            "id": r[0],
            "rule_id": r[1],
            "rule_name": r[2],
            "trigger_type": r[3],
            "status": r[4],
            "summary": r[5],
            "executed_at": r[6].isoformat() if r[6] else "",
        }
        for r in rows
    ]
    return {"rules": AUTOMATION_RULES, "logs": logs}


@app.post("/automations/run/{rule_id}")
async def run_automation(rule_id: str) -> dict[str, Any]:
    """Execute a single automation rule."""
    return await _execute_rule(rule_id, trigger_type="manual")


@app.post("/automations/run-all")
async def run_all_automations() -> dict[str, Any]:
    """Execute all automation rules in sequence."""
    results = []
    for r in AUTOMATION_RULES:
        results.append(await _execute_rule(r["id"], trigger_type="batch"))
    return {"executed": len(results), "results": results}
