import os
import re
from typing import Any

import httpx

from app.vision_search import (
    FALLBACK_MODE,
    OLLAMA_BASE_URL,
    get_embedding,
    search_products_by_embedding,
    search_products_fallback,
)

LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:8b")

SYSTEM_PROMPT = (
    "Tu es le conseiller vendeur IA de la jardinerie Mes Plantes. "
    "Réponds en français, de manière chaleureuse, concise et pratique. "
    "Appuie-toi uniquement sur les produits fournis dans le contexte (nom, SKU, prix, stock, "
    "rayon, exposition et sécurité animaux). "
    "Si un produit est toxique pour les animaux (pet_safe=False), signale-le clairement. "
    "Si aucun produit du catalogue ne correspond, dis-le honnêtement sans inventer de référence."
)


def _format_catalog_context(products: list[dict[str, Any]]) -> str:
    if not products:
        return "Aucun produit correspondant trouvé dans le catalogue."

    lines = []
    for p in products:
        pet = "Oui (sans danger)" if p.get("pet_safe") else "Non (toxique)"
        tags = ", ".join(p.get("tags") or [])
        lines.append(
            f"- [{p['sku']}] {p['name']} | Catégorie: {p['category']} | "
            f"Rayon: {p['aisle']} | Prix: {p['price']:.2f}€ | Stock: {p['stock_qty']} | "
            f"Exposition: {p['exposure']} | Animaux: {pet} | Tags: {tags}"
        )
    return "\n".join(lines)


def _clean_qwen_response(text: str) -> str:
    """Remove optional <think>...</think> blocks emitted by Qwen3 models."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def _fallback_answer(question: str, products: list[dict[str, Any]]) -> str:
    if not products:
        return (
            "Je n'ai trouvé aucun produit correspondant dans le catalogue pour le moment. "
            "N'hésitez pas à demander conseil à un vendeur en magasin."
        )
    bullets = "\n".join(
        f"- **{p['name']}** (`{p['sku']}`) — {p['price']:.2f}€, rayon *{p['aisle']}* "
        f"(stock : {p['stock_qty']})"
        for p in products
    )
    return (
        f"Voici les produits du catalogue Mes Plantes les plus pertinents pour votre demande :\n\n"
        f"{bullets}"
    )


async def retrieve_relevant_products(question: str, limit: int = 4) -> tuple[list[dict[str, Any]], str]:
    """Retrieve matching products via pgvector, falling back to keyword search."""
    try:
        embedding = await get_embedding(question)
        products = search_products_by_embedding(embedding, limit=limit)
        if products:
            return products, "vector"
    except Exception:
        if not FALLBACK_MODE:
            raise

    products = search_products_fallback(question, limit=limit)
    return products, "keyword_fallback"


async def advise_customer(
    question: str,
    limit: int = 4,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Answer a customer question using multi-turn history, RAG over PostgreSQL, and qwen3:8b."""
    history = history or []
    recent_history = [
        {"role": m["role"], "content": m["content"]}
        for m in history[-6:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    # Enrich retrieval query with the previous user message for follow-up questions
    prev_user_msgs = [m["content"] for m in recent_history if m["role"] == "user"]
    retrieval_query = f"{prev_user_msgs[-1]} {question}" if prev_user_msgs else question

    products, retrieval_mode = await retrieve_relevant_products(retrieval_query, limit=limit)
    context = _format_catalog_context(products)

    user_message = (
        f"Catalogue disponible :\n{context}\n\n"
        f"Question du client : {question}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *recent_history,
        {"role": "user", "content": user_message},
    ]

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "messages": messages,
                    "stream": False,
                },
            )
            response.raise_for_status()
            raw_answer = response.json()["message"]["content"]
            answer = _clean_qwen_response(raw_answer)
            llm_mode = "ollama"
    except Exception:
        if not FALLBACK_MODE:
            raise
        answer = _fallback_answer(question, products)
        llm_mode = "fallback"

    return {
        "answer": answer,
        "retrieval_mode": retrieval_mode,
        "llm_mode": llm_mode,
        "products": products,
    }
