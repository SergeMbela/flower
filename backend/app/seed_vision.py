import asyncio
import mimetypes
import os
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from app.storage import CATALOG_BUCKET, object_exists, upload_bytes
from app.vision_search import DATABASE_URL, get_embedding

DATA_IMAGES_DIR = Path(os.getenv("DATA_IMAGES_DIR", "/app/data/images"))


def build_product_document(
    name: str,
    category: str,
    aisle: str,
    exposure: str,
    pet_safe: bool,
    tags: list[str] | None,
) -> str:
    """Build a descriptive French text for product embedding generation."""
    pet_text = "sans danger pour les animaux" if pet_safe else "toxique pour les animaux"
    tags_text = ", ".join(tags or [])
    return (
        f"Produit: {name}. "
        f"Catégorie: {category}. "
        f"Rayon: {aisle}. "
        f"Exposition: {exposure}. "
        f"Sécurité animaux: {pet_text}. "
        f"Mots-clés: {tags_text}."
    )


async def seed_embeddings(force: bool = False) -> int:
    """Compute and store 768-d embeddings in PostgreSQL for products."""
    where_clause = "" if force else "WHERE embedding IS NULL"
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, sku, name, category, aisle, exposure, pet_safe, tags
                FROM products
                {where_clause}
                ORDER BY id
                """
            )
            rows = cur.fetchall()

        updated = 0
        for prod_id, sku, name, category, aisle, exposure, pet_safe, tags in rows:
            doc = build_product_document(name, category, aisle, exposure, pet_safe, tags)
            vector = await get_embedding(doc)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE products SET embedding = %s WHERE id = %s",
                    (vector, prod_id),
                )
            conn.commit()
            updated += 1
            print(f"[seed_vision] Embedding mis à jour pour {sku} ({name})")

    return updated


def seed_catalog_images(images_dir: Path = DATA_IMAGES_DIR) -> int:
    """Upload local catalog images (if present) to MinIO catalog-images bucket."""
    if not images_dir.exists():
        return 0

    uploaded = 0
    for path in sorted(images_dir.iterdir()):
        if not path.is_file():
            continue
        key = path.name
        if object_exists(key, bucket=CATALOG_BUCKET):
            continue
        content_type, _ = mimetypes.guess_type(path.name)
        upload_bytes(
            key=key,
            data=path.read_bytes(),
            bucket=CATALOG_BUCKET,
            content_type=content_type or "application/octet-stream",
        )
        uploaded += 1
        print(f"[seed_vision] Image envoyée vers MinIO: {key}")

    return uploaded


async def main() -> None:
    uploaded = seed_catalog_images()
    updated = await seed_embeddings(force=False)
    print(f"[seed_vision] Terminé : {uploaded} image(s) uploadée(s), {updated} embedding(s) généré(s).")


if __name__ == "__main__":
    asyncio.run(main())
