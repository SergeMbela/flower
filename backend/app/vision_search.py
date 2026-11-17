import base64
import io
import os
import re
from typing import Any

import httpx
import psycopg
from pgvector.psycopg import register_vector
from PIL import Image

from app.storage import get_public_url

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://famiflora:famiflora_secret@postgres:5432/famiflora_db",
)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
VISION_MODEL = os.getenv("VISION_MODEL", "llava:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
FALLBACK_MODE = os.getenv("FALLBACK_MODE", "true").lower() == "true"

VISION_PROMPT = (
    "Tu es un expert en jardinerie et décoration (Mes Plantes). "
    "Identifie l'objet ou la plante sur cette image en français. "
    "Décris brièvement : le nom probable, la catégorie (plante d'intérieur, plante vivace, "
    "arbre fruitier, terreau, protection hiver, décoration, Noël), l'exposition (soleil, ombre) "
    "et les mots-clés utiles pour retrouver ce produit en magasin."
)

DOCTOR_PROMPT = (
    "Tu es le « Docteur Plantes » de la jardinerie Mes Plantes. "
    "Observe cette plante en détail et réponds en français de manière structurée et concise :\n"
    "1. État général et symptômes visibles (feuilles jaunies, pointes sèches, taches, coup de froid, manque de lumière ou d'arrosage).\n"
    "2. Cause probable du problème.\n"
    "3. Conseils pratiques de soin immédiats (arrosage, exposition, rempotage, protection contre le gel).\n"
    "4. Type de produit utile en magasin (ex. terreau horticole pour rempotage, voile d'hivernage contre le gel)."
)


def prepare_image_base64(image_bytes: bytes, max_size: int = 1024) -> str:
    """Resize an image to max_size and encode it as base64 JPEG for Ollama Vision."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        rgb = img.convert("RGB")
        rgb.thumbnail((max_size, max_size))
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def describe_image(image_bytes: bytes, prompt: str = VISION_PROMPT) -> str:
    """Use Ollama vision model (llava:7b) to describe or diagnose an uploaded image."""
    image_b64 = prepare_image_base64(image_bytes)
    payload = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
        response.raise_for_status()
        data = response.json()
    return (data.get("response") or "").strip()


async def get_embedding(text: str) -> list[float]:
    """Generate a 768-d embedding vector via Ollama nomic-embed-text."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        if response.status_code == 404:
            # Fallback for older Ollama versions
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
            )
            response.raise_for_status()
            return response.json()["embedding"]

        response.raise_for_status()
        data = response.json()
        return data["embeddings"][0]


def _format_product_row(row: tuple[Any, ...], score: float | None = None) -> dict[str, Any]:
    (
        prod_id,
        sku,
        name,
        category,
        aisle,
        price,
        stock_qty,
        exposure,
        pet_safe,
        image_key,
        tags,
    ) = row
    item = {
        "id": prod_id,
        "sku": sku,
        "name": name,
        "category": category,
        "aisle": aisle,
        "price": float(price),
        "stock_qty": stock_qty,
        "exposure": exposure,
        "pet_safe": pet_safe,
        "image_key": image_key,
        "image_url": get_public_url(image_key) if image_key else None,
        "tags": tags or [],
    }
    if score is not None:
        item["score"] = round(float(score), 4)
    return item


def search_products_by_embedding(embedding: list[float], limit: int = 5) -> list[dict[str, Any]]:
    """Find closest products in PostgreSQL using pgvector cosine similarity."""
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, sku, name, category, aisle, price, stock_qty,
                       exposure, pet_safe, image_key, tags,
                       1 - (embedding <=> %s::vector) AS score
                FROM products
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (embedding, embedding, limit),
            )
            rows = cur.fetchall()
    return [_format_product_row(row[:-1], score=row[-1]) for row in rows]


def search_products_fallback(query_text: str, limit: int = 5) -> list[dict[str, Any]]:
    """Keyword fallback search in PostgreSQL when embeddings are missing or Ollama fails."""
    tokens = [t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", query_text.lower())][:8]
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            if not tokens:
                cur.execute(
                    """
                    SELECT id, sku, name, category, aisle, price, stock_qty,
                           exposure, pet_safe, image_key, tags
                    FROM products
                    ORDER BY id
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [_format_product_row(row) for row in cur.fetchall()]

            patterns = [f"%{tok}%" for tok in tokens]
            cur.execute(
                """
                SELECT id, sku, name, category, aisle, price, stock_qty,
                       exposure, pet_safe, image_key, tags
                FROM products
                WHERE name ILIKE ANY(%s)
                   OR category ILIKE ANY(%s)
                   OR exposure ILIKE ANY(%s)
                   OR EXISTS (
                       SELECT 1 FROM unnest(tags) AS t WHERE t ILIKE ANY(%s)
                   )
                ORDER BY id
                LIMIT %s
                """,
                (patterns, patterns, patterns, patterns, limit),
            )
            return [_format_product_row(row) for row in cur.fetchall()]


async def search_by_image(
    image_bytes: bytes,
    limit: int = 5,
    mode_type: str = "search",
) -> dict[str, Any]:
    """Describe or diagnose an uploaded image with llava:7b and search matching products."""
    description = ""
    mode = "vector"
    prompt = DOCTOR_PROMPT if mode_type == "doctor" else VISION_PROMPT
    try:
        description = await describe_image(image_bytes, prompt=prompt)
        embedding = await get_embedding(description)
        results = search_products_by_embedding(embedding, limit=limit)
        if not results and FALLBACK_MODE:
            mode = "keyword_fallback"
            results = search_products_fallback(description, limit=limit)
    except Exception:
        if not FALLBACK_MODE:
            raise
        mode = "fallback"
        results = search_products_fallback(description, limit=limit)

    return {
        "analysis_mode": mode_type,
        "mode": mode,
        "vision_description": description,
        "results": results,
    }
