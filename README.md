# 🌿 Mes Plantes AI — POC Retail & Jardinerie Intelligente

Proof of Concept (POC) full-stack d'assistant magasin et de pilotage retail pour une jardinerie, combinant **LLM local (`qwen3:8b`)**, **Vision multimodale (`llava:7b`)**, **RAG vectoriel (`pgvector` + `nomic-embed-text`)**, **stockage objet S3 (`MinIO`)** et **tableaux de bord opérationnels**, le tout orchestré via **Docker Compose**.

---

## ✨ Fonctionnalités principales

### 🤖 IA & Recherche Sémantique (modèles réels via Ollama)
- **💬 Conseiller IA multi-tours** (`qwen3:8b` + `pgvector`) : répond aux questions clients en s'appuyant sur le catalogue réel (prix, stock, rayon, exposition, sécurité animaux `pet_safe`), avec mémoire de conversation pour les questions de suivi.
- **📷 Recherche par Photo & 🩺 Docteur Plantes** (`llava:7b` + `MinIO`) :
  - *Identifier un produit* : analyse une photo et retrouve les articles correspondants via `nomic-embed-text` + `pgvector`.
  - *Docteur Plantes* : diagnostique l'état d'une plante (feuilles jaunies, coup de froid, stress) et recommande les produits adaptés (terreau, voile d'hivernage…).
- **✨ Synthèse IA dirigeant** : génère un plan d'action court à partir des prévisions de rupture.

### 📊 Pilotage Magasin & Expérience Client
- **🪴 Catalogue** : recherche/filtrage instantané sur 20 produits, bouton de génération des embeddings.
- **🛒 Liste d'achats & parcours multi-rayons** : ajout d'articles, calcul du total estimé et tri automatique de l'itinéraire en magasin (`Entrée → A → B → C → D → Caisses`).
- **🗺️ Fréquentation (Heatmap)** : carte des zones du magasin (`Serre Chaude`, `Allée Extérieure`, `Allée C`, `Hall Déco`, `Caisses`) avec simulation de flux temps réel, alertes de réapprovisionnement et top questions clients.
- **🔮 Prédictions** : simulation de scénarios météo/saison (*Semaine standard*, *Vague de gel*, *Week-end ensoleillé*), prévision d'affluence 7 jours, cross-sell et calcul des jours avant rupture.
- **📦 Achats / Ventes & Stocks** : KPIs, graphique par catégorie, tableau des stocks, historique PostgreSQL (`stock_movements`), saisie manuelle et simulation automatique d'achats/ventes.
- **💧 Capteurs Serre (IoT)** : suivi humidité du sol/air et température par zone, alerte d'arrosage, bouton d'irrigation et endpoint d'ingestion `POST /sensors/ingest`.
- **♻️ Anti-Gaspillage** : détection des plantes à rotation lente ou en zone sèche et activation de promos flash week-end (`-20%` / `-30%`).
- **📈 Monitoring Statistique** :
  - **Descriptives & dispersion** : moyenne, médiane, écart-type ($\sigma$), quartiles (Q1, Q3, IQR) sur les ventes et les stocks.
  - **Segmentation ABC (Pareto 80/20)** : classement des produits par contribution au chiffre d'affaires.
  - **Stock de sécurité & Point de commande (ROP)** : calcul probabiliste ($SS = Z \times \sigma_D \times \sqrt{L}$ à 95 % de taux de service).
  - **Corrélation de Pearson ($r$)** : lien entre affluence rayon (heatmap) et volume des ventes.
  - **Détection d'anomalies (Z-score)** : repérage des dérives sur les capteurs IoT et les stocks.
  - **Intervalles de confiance (95 %)** : fourchette basse/haute sur la prévision d'affluence à 7 jours.
- **⚙️ Automatisations (Moteur de règles)** :
  - **Réapprovisionnement ROP automatique** : commande fournisseur auto dès que `stock_qty <= reorder_point`.
  - **Arrosage IoT automatique** : irrigation auto des zones sèches (`soil_moisture_pct < target_min_moisture`).
  - **Démarque Flash Anti-Gaspillage** : application auto des remises `-20 %` / `-30 %` sur les plantes à risque.
  - **Synchronisation Embeddings IA** : indexation automatique `nomic-embed-text` des nouveaux produits.
  - **Briefing matinal IA (`qwen3:8b`)** : synthèse quotidienne (ruptures, zones sèches, anomalies Z-score).

---

## 🏗️ Architecture

```text
┌──────────────────────┐      /api/*      ┌──────────────────────┐
│  Frontend (Nginx)    │ ───────────────▶ │   Backend (FastAPI)  │
│  http://localhost:8100│                 │  http://localhost:8020│
└──────────────────────┘                  └──────────┬───────────┘
                                                     │
                 ┌───────────────────────────────────┼───────────────────────────────────┐
                 ▼                                   ▼                                   ▼
   ┌──────────────────────────┐        ┌──────────────────────────┐        ┌──────────────────────────┐
   │  PostgreSQL + pgvector   │        │      MinIO (S3 API)      │        │    Ollama (GPU/CPU)      │
   │  Port 5452 (famiflora_db)│        │  Ports 9020 / 9021       │        │    Port 11434            │
   │  products, stock_movements│       │  catalog-images,         │        │  qwen3:8b, llava:7b,     │
   │  vector(768)             │        │  client-uploads          │        │  nomic-embed-text        │
   └──────────────────────────┘        └──────────────────────────┘        └──────────────────────────┘
```

### Transparence technique (POC)
- **IA réelle** : embeddings (`nomic-embed-text`), recherche cosinus (`pgvector`), chat RAG (`qwen3:8b`), analyse/diagnostic d'images (`llava:7b`).
- **Données simulées (Mock)** : la heatmap de fréquentation, les scénarios météo/saison et le flux automatique de ventes/achats reposent sur des règles métier et des mouvements simulés en base PostgreSQL.

---

## 📁 Structure du projet

```text
famiflora-ai-docker-poc/
├── docker-compose.yml
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # Endpoints FastAPI (RAG, Vision, Heatmap, Prédictions, Stocks)
│       ├── llm_advisor.py     # Conseiller RAG multi-tours (qwen3:8b)
│       ├── vision_search.py   # Vision & diagnostic (llava:7b) + recherche pgvector
│       ├── storage.py         # Client S3 MinIO (boto3)
│       ├── seed_vision.py     # Génération des embeddings et upload d'images catalogue
│       └── simulate_stock.py  # CLI de simulation de stocks, ventes et réapprovisionnement
└── frontend/
    ├── Dockerfile
    ├── nginx.conf             # Serveur statique + reverse proxy /api/
    ├── index.html
    ├── styles.css
    └── app.js
```

---

## 🚀 Démarrage rapide

### 1. Prérequis
- Docker & Docker Compose
- Ollama accessible sur le port `11434` avec les modèles suivants :
  ```bash
  ollama pull qwen3:8b
  ollama pull llava:7b
  ollama pull nomic-embed-text
  ```

### 2. Lancer la stack
```bash
docker compose up -d --build
```

Services disponibles :
- **Interface Web** : http://localhost:8100
- **API FastAPI (Swagger)** : http://localhost:8020/docs
- **Console MinIO** : http://localhost:9021 (`minioadmin` / `minioadmin123`)
- **PostgreSQL** : `localhost:5452` (`famiflora` / `famiflora_secret` / `famiflora_db`)

### 3. Générer les embeddings & simuler les stocks (CLI)
```bash
# Générer les vecteurs 768D des produits dans pgvector
docker exec famiflora_api python -m app.seed_vision

# Consulter les stocks
docker exec famiflora_api python -m app.simulate_stock status

# Simuler 3 jours de ventes sous scénario "vague de gel"
docker exec famiflora_api python -m app.simulate_stock simulate --days 3 --scenario frost

# Ajouter du stock
docker exec famiflora_api python -m app.simulate_stock add FAM-PL-004 25
```

---

## 🔌 Principaux endpoints API

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Vérification API & PostgreSQL |
| `GET` | `/products` | Liste des produits du catalogue |
| `POST` | `/advisor` | Conseiller IA RAG multi-tours (`qwen3:8b`) |
| `POST` | `/vision-search` | Identification ou diagnostic photo (`analysis_mode=search\|doctor`) |
| `POST` | `/seed-vision` | Indexation vectorielle des produits |
| `GET` | `/store-heatmap` | Fréquentation par rayon (`morning\|afternoon\|weekend`) |
| `GET` | `/stock-alerts` | Alertes de stock croisées avec l'affluence |
| `GET` | `/predictions` | Prévisions de rupture, affluence 7j et cross-sell |
| `POST` | `/predictions/ai-brief` | Synthèse managériale générée par `qwen3:8b` |
| `GET` | `/inventory-overview` | KPIs, données graphiques et mouvements de stock |
| `POST` | `/inventory-movement` | Enregistrement manuel d'un achat ou d'une vente |
| `POST` | `/inventory-auto-simulate` | Simulation automatique d'achats / cycle ventes+achats |
| `GET` | `/sensors` | Mesures IoT (humidité sol/air, température) par zone |
| `POST` | `/sensors/ingest` | Ingestion de mesures capteurs physiques |
| `GET` | `/waste-markdowns` | Détection anti-gaspillage & remises conseillées |
| `POST` | `/waste-markdowns/apply` | Activation / retrait d'une promo flash |
| `GET` | `/monitoring/stats` | Statistiques avancées (Pareto ABC, stock sécurité, Pearson, Z-score) |
| `GET` | `/automations` | Liste des règles d'automatisation & historique d'exécution |
| `POST` | `/automations/run/{rule_id}` | Exécution d'une règle d'automatisation (`auto_rop_reorder`, etc.) |
| `POST` | `/automations/run-all` | Exécution groupée de toutes les automatisations |
