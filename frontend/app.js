const API_BASE = "/api";

let catalogCache = [];

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderProducts(container, products) {
  if (!products || products.length === 0) {
    container.innerHTML = `<p class="product-meta">Aucun produit trouvé.</p>`;
    return;
  }

  container.innerHTML = products
    .map((p) => {
      const petBadge = p.pet_safe
        ? `<span class="badge">🐾 Pet-safe</span>`
        : `<span class="badge warn">⚠️ Toxique animaux</span>`;
      const scoreBadge =
        typeof p.score === "number"
          ? `<span class="badge">Score ${(p.score * 100).toFixed(0)}%</span>`
          : p.discount_pct > 0
          ? `<span class="badge warn">🏷️ -${p.discount_pct}% Flash</span>`
          : "";
      const priceHtml =
        p.discount_pct > 0
          ? `<span style="text-decoration:line-through;color:var(--muted);font-size:0.85rem;margin-right:0.4rem;">${Number(p.price).toFixed(2)} €</span>${Number(p.discounted_price).toFixed(2)} €`
          : `${Number(p.price).toFixed(2)} €`;
      const tags = (p.tags || [])
        .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
        .join("");

      return `
        <article class="product-card">
          <div class="meta-row">
            <span class="product-sku">${escapeHtml(p.sku)}</span>
            ${scoreBadge || petBadge}
          </div>
          <h4>${escapeHtml(p.name)}</h4>
          <div class="product-price">${priceHtml}</div>
          <div class="product-meta">
            <div><strong>Catégorie :</strong> ${escapeHtml(p.category)}</div>
            <div><strong>Rayon :</strong> ${escapeHtml(p.aisle)}</div>
            <div><strong>Exposition :</strong> ${escapeHtml(p.exposure)}</div>
            <div><strong>Stock :</strong> ${escapeHtml(p.stock_qty)} unités</div>
          </div>
          <div class="tag-list">${tags}</div>
          <div class="card-actions">
            <button type="button" class="secondary" onclick="openRouteModal('${escapeHtml(p.name)}', '${escapeHtml(p.aisle)}')">📍 Itinéraire</button>
            <button type="button" onclick="addToShoppingList('${escapeHtml(p.sku)}', '${escapeHtml(p.name)}', '${escapeHtml(p.aisle)}', ${Number(p.price)})">🛒 + Liste</button>
          </div>
        </article>
      `;
    })
    .join("");
}

async function checkHealth() {
  const pill = document.getElementById("api-status");
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error();
    pill.textContent = "API connectée";
    pill.classList.remove("error");
  } catch {
    pill.textContent = "API indisponible";
    pill.classList.add("error");
  }
}

async function loadCatalog() {
  const container = document.getElementById("catalog-products");
  try {
    const res = await fetch(`${API_BASE}/products`);
    catalogCache = await res.json();
    filterCatalog();
  } catch {
    container.innerHTML = `<p class="product-meta">Impossible de charger le catalogue.</p>`;
  }
}

function filterCatalog() {
  const q = document.getElementById("catalog-filter").value.trim().toLowerCase();
  const filtered = !q
    ? catalogCache
    : catalogCache.filter((p) =>
        [p.sku, p.name, p.category, p.aisle, p.exposure, ...(p.tags || [])]
          .join(" ")
          .toLowerCase()
          .includes(q)
      );
  renderProducts(document.getElementById("catalog-products"), filtered);
}

// Navigation par onglets
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// Suggestions rapides
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.getElementById("advisor-input").value = chip.dataset.q;
    document.getElementById("advisor-form").requestSubmit();
  });
});

// Conseiller IA (multi-tours avec mémoire)
let advisorHistory = [];

function renderAdvisorThread() {
  const thread = document.getElementById("advisor-thread");
  thread.innerHTML = advisorHistory
    .map(
      (msg) =>
        `<div class="chat-bubble ${msg.role === "user" ? "user" : "assistant"}">${escapeHtml(msg.content)}</div>`
    )
    .join("");
}

document.getElementById("advisor-reset-btn").addEventListener("click", () => {
  advisorHistory = [];
  document.getElementById("advisor-thread").innerHTML = "";
  document.getElementById("advisor-products").innerHTML = "";
  document.getElementById("advisor-result").classList.add("hidden");
  document.getElementById("advisor-input").value = "";
});

document.getElementById("advisor-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("advisor-input");
  const btn = document.getElementById("advisor-submit");
  const resultBox = document.getElementById("advisor-result");
  const question = input.value.trim();
  if (!question) return;

  const historyToSend = [...advisorHistory];
  advisorHistory.push({ role: "user", content: question });
  renderAdvisorThread();
  resultBox.classList.remove("hidden");
  input.value = "";

  btn.disabled = true;
  btn.textContent = "Réflexion…";
  try {
    const res = await fetch(`${API_BASE}/advisor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, limit: 4, history: historyToSend }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erreur API");

    advisorHistory.push({ role: "assistant", content: data.answer });
    renderAdvisorThread();
    document.getElementById("advisor-meta").textContent = `${data.llm_mode} · ${data.retrieval_mode} (${advisorHistory.length / 2} tour(s))`;
    renderProducts(document.getElementById("advisor-products"), data.products);
  } catch (err) {
    advisorHistory.pop();
    renderAdvisorThread();
    alert(`Erreur conseiller IA : ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Demander";
  }
});

// Aperçu image
document.getElementById("vision-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  const preview = document.getElementById("vision-preview");
  if (file) {
    preview.src = URL.createObjectURL(file);
    preview.classList.remove("hidden");
  } else {
    preview.classList.add("hidden");
  }
});

// Recherche visuelle & Docteur Plantes
document.getElementById("vision-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("vision-file");
  const btn = document.getElementById("vision-submit");
  const resultBox = document.getElementById("vision-result");
  const selectedMode = document.querySelector('input[name="vision-mode"]:checked')?.value || "search";

  if (!fileInput.files[0]) return;
  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  btn.disabled = true;
  btn.textContent = selectedMode === "doctor" ? "Diagnostic…" : "Analyse…";
  try {
    const res = await fetch(
      `${API_BASE}/vision-search?limit=5&analysis_mode=${encodeURIComponent(selectedMode)}`,
      {
        method: "POST",
        body: formData,
      }
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erreur API");

    const isDoctor = data.analysis_mode === "doctor";
    document.getElementById("vision-title").textContent = isDoctor
      ? "🩺 Diagnostic Docteur Plantes (llava:7b)"
      : "🔍 Analyse visuelle (llava:7b)";
    document.getElementById("vision-products-title").textContent = isDoctor
      ? "Produits recommandés pour le traitement"
      : "Produits correspondants";
    document.getElementById("vision-meta").textContent = `${data.analysis_mode} · ${data.mode}`;
    document.getElementById("vision-description").textContent =
      data.vision_description || "Aucune description générée.";
    renderProducts(document.getElementById("vision-products"), data.results);
    resultBox.classList.remove("hidden");
  } catch (err) {
    alert(`Erreur recherche visuelle : ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyser l'image";
  }
});

// Catalogue & embeddings
document.getElementById("catalog-filter").addEventListener("input", filterCatalog);
document.getElementById("seed-btn").addEventListener("click", async () => {
  const btn = document.getElementById("seed-btn");
  const status = document.getElementById("seed-status");
  btn.disabled = true;
  status.classList.remove("hidden");
  status.textContent = "Génération des embeddings via nomic-embed-text…";
  try {
    const res = await fetch(`${API_BASE}/seed-vision`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erreur");
    status.textContent = `Terminé : ${data.updated_embeddings} embedding(s) généré(s), ${data.uploaded_images} image(s) envoyée(s).`;
    await loadCatalog();
  } catch (err) {
    status.textContent = `Erreur : ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

checkHealth();
loadCatalog();

// --- Heatmap de fréquentation (Mock) ---
let heatmapData = null;
let heatmapTimer = null;

function heatColor(pct) {
  // 0% -> vert (#2d6a4f), 60% -> ambre (#e07a22), 100% -> rouge (#c1121f)
  const clamped = Math.max(0, Math.min(100, pct));
  if (clamped < 55) {
    return "#2d8a5b";
  }
  if (clamped < 80) {
    return "#d97724";
  }
  return "#c92a2a";
}

function renderHeatmap(data) {
  heatmapData = data;
  document.getElementById("kpi-visitors").textContent = data.total_visitors;
  document.getElementById("kpi-hottest").textContent = data.hottest_zone;
  document.getElementById("kpi-dwell").textContent = `${data.avg_dwell_min} min`;

  const grid = document.getElementById("heatmap-grid");
  grid.innerHTML = data.zones
    .map(
      (z) => `
      <div class="zone-tile" data-zone-id="${escapeHtml(z.id)}" style="background:${heatColor(z.occupancy_pct)}">
        <div class="zone-header">
          <span class="zone-title">${escapeHtml(z.label)}</span>
          <span class="zone-pct">${z.occupancy_pct}%</span>
        </div>
        <div class="zone-stats">
          👥 ${z.visitors} / ${z.capacity} visiteurs · ⏱ ${z.dwell_min} min
        </div>
        <div class="zone-stats" style="margin-top:0.3rem;">
          📦 ${z.products.length} produit(s) référencé(s)
        </div>
      </div>
    `
    )
    .join("");

  grid.querySelectorAll(".zone-tile").forEach((tile) => {
    tile.addEventListener("click", () => {
      grid.querySelectorAll(".zone-tile").forEach((t) => t.classList.remove("selected"));
      tile.classList.add("selected");
      const zone = heatmapData.zones.find((z) => z.id === tile.dataset.zoneId);
      showZoneDetail(zone);
    });
  });
}

function showZoneDetail(zone) {
  const box = document.getElementById("heatmap-detail");
  const title = document.getElementById("heatmap-detail-title");
  const list = document.getElementById("heatmap-detail-products");
  if (!zone) return;

  title.textContent = `${zone.label} — ${zone.visitors} visiteurs (${zone.occupancy_pct}%)`;
  if (!zone.products || zone.products.length === 0) {
    list.innerHTML = `<p class="product-meta">Zone de passage / service (aucun produit catalogue associé).</p>`;
  } else {
    list.innerHTML = zone.products
      .map(
        (p) => `
        <article class="product-card">
          <div class="meta-row">
            <span class="product-sku">${escapeHtml(p.sku)}</span>
            <span class="badge">${escapeHtml(p.aisle)}</span>
          </div>
          <h4>${escapeHtml(p.name)}</h4>
          <div class="product-price">${Number(p.price).toFixed(2)} €</div>
          <div class="product-meta">Stock : ${escapeHtml(p.stock_qty)} unités</div>
          <div class="card-actions">
            <button type="button" class="secondary" onclick="openRouteModal('${escapeHtml(p.name)}', '${escapeHtml(p.aisle)}')">📍 Itinéraire</button>
            <button type="button" onclick="addToShoppingList('${escapeHtml(p.sku)}', '${escapeHtml(p.name)}', '${escapeHtml(p.aisle)}', ${Number(p.price)})">🛒 + Liste</button>
          </div>
        </article>
      `
      )
      .join("");
  }
  box.classList.remove("hidden");
}

async function loadHeatmap() {
  const slot = document.getElementById("heatmap-slot").value;
  try {
    const res = await fetch(`${API_BASE}/store-heatmap?slot=${encodeURIComponent(slot)}`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    renderHeatmap(data);
  } catch {
    document.getElementById("heatmap-grid").innerHTML =
      `<p class="product-meta">Impossible de charger la fréquentation.</p>`;
  }
}

document.getElementById("heatmap-slot").addEventListener("change", loadHeatmap);
document.getElementById("heatmap-live-btn").addEventListener("click", () => {
  const btn = document.getElementById("heatmap-live-btn");
  if (heatmapTimer) {
    clearInterval(heatmapTimer);
    heatmapTimer = null;
    btn.textContent = "▶ Simuler flux temps réel";
    return;
  }
  btn.textContent = "⏸ Arrêter simulation";
  heatmapTimer = setInterval(() => {
    if (!heatmapData) return;
    let total = 0;
    const zones = heatmapData.zones.map((z) => {
      const delta = Math.floor(Math.random() * 7) - 3;
      const visitors = Math.max(2, Math.min(z.capacity, z.visitors + delta));
      total += visitors;
      return {
        ...z,
        visitors,
        occupancy_pct: Math.round((visitors / z.capacity) * 100),
      };
    });
    const hottest = zones.reduce((a, b) => (a.occupancy_pct >= b.occupancy_pct ? a : b));
    renderHeatmap({ ...heatmapData, total_visitors: total, hottest_zone: hottest.label, zones });
  }, 1500);
});

loadHeatmap();

// --- Alertes de réapprovisionnement & Tendances IA ---
async function loadStockAlerts() {
  const container = document.getElementById("stock-alerts-list");
  try {
    const res = await fetch(`${API_BASE}/stock-alerts?threshold=25`);
    const alerts = await res.json();
    if (!alerts.length) {
      container.innerHTML = `<p class="product-meta">Aucune alerte de stock.</p>`;
      return;
    }
    container.innerHTML = alerts
      .map(
        (a) => `
        <div class="insight-item">
          <div class="meta-row">
            <strong>${escapeHtml(a.name)}</strong>
            <span class="badge severity-${escapeHtml(a.severity)}">${escapeHtml(a.severity.toUpperCase())}</span>
          </div>
          <div class="product-meta">
            Stock : <strong>${a.stock_qty}</strong> unités · Rayon ${escapeHtml(a.aisle)} · Affluence zone : <strong>${a.zone_occupancy_pct}%</strong>
          </div>
        </div>
      `
      )
      .join("");
  } catch {
    container.innerHTML = `<p class="product-meta">Impossible de charger les alertes.</p>`;
  }
}

async function loadAiTrends() {
  const container = document.getElementById("ai-trends-list");
  try {
    const res = await fetch(`${API_BASE}/ai-trends`);
    const trends = await res.json();
    container.innerHTML = trends
      .map(
        (t) => `
        <div class="insight-item clickable" data-q="${escapeHtml(t.sample_question)}">
          <div class="meta-row">
            <strong>${escapeHtml(t.topic)}</strong>
            <span class="badge">${t.count} req.</span>
          </div>
          <div class="product-meta">« ${escapeHtml(t.sample_question)} »</div>
        </div>
      `
      )
      .join("");

    container.querySelectorAll(".insight-item.clickable").forEach((el) => {
      el.addEventListener("click", () => {
        document.querySelector('.tab[data-tab="advisor"]').click();
        document.getElementById("advisor-input").value = el.dataset.q;
        document.getElementById("advisor-form").requestSubmit();
      });
    });
  } catch {
    container.innerHTML = `<p class="product-meta">Impossible de charger les tendances.</p>`;
  }
}

// --- Simulateur de parcours / guidage rayon ---
const STORE_ZONES = [
  { id: "entree", label: "Entrée & Accueil", time: "0 min" },
  { id: "serre", label: "Serre Chaude (A01–A15)", prefix: "Serre Chaude", time: "1 min 30" },
  { id: "exterieur", label: "Allée Extérieure (B01–B20)", prefix: "Allée Extérieure", time: "2 min 15" },
  { id: "terreaux", label: "Terreaux & Hiver (C01–C12)", prefix: "Allée C", time: "1 min 45" },
  { id: "deco", label: "Hall Déco & Noël (D01–D12)", prefix: "Hall Déco", time: "2 min 00" },
  { id: "caisses", label: "Caisses", time: "—" },
];

window.openRouteModal = function (productName, aisle) {
  const modal = document.getElementById("route-modal");
  const target = STORE_ZONES.find((z) => z.prefix && aisle.startsWith(z.prefix)) || STORE_ZONES[1];

  document.getElementById("route-title").textContent = `📍 Itinéraire vers ${aisle}`;
  document.getElementById("route-subtitle").textContent = `${productName} · Temps estimé depuis l'entrée : ~${target.time}`;

  document.getElementById("route-minimap").innerHTML = STORE_ZONES.map((z) => {
    const cls = z.id === "entree" ? "start" : z.id === target.id ? "target" : "";
    const tag = z.id === "entree" ? " (Départ)" : z.id === target.id ? " (Arrivée)" : "";
    return `<div class="minimap-cell ${cls}">${escapeHtml(z.label)}${tag}</div>`;
  }).join("");

  document.getElementById("route-steps").innerHTML = `
    <li>Départ depuis l'<strong>Entrée &amp; Accueil</strong>.</li>
    <li>Suivez la signalétique vers la zone <strong>${escapeHtml(target.label)}</strong>.</li>
    <li>Rejoignez le rayon exact : <strong>${escapeHtml(aisle)}</strong>.</li>
  `;

  modal.classList.remove("hidden");
};

document.getElementById("route-close").addEventListener("click", () => {
  document.getElementById("route-modal").classList.add("hidden");
});
document.getElementById("route-modal").addEventListener("click", (e) => {
  if (e.target.id === "route-modal") {
    e.currentTarget.classList.add("hidden");
  }
});

loadStockAlerts();
loadAiTrends();

// --- Prédictions (Ruptures, Affluence 7j, Cross-sell, Synthèse IA) ---
async function loadPredictions() {
  const scenario = document.getElementById("pred-scenario").value;
  try {
    const res = await fetch(`${API_BASE}/predictions?scenario=${encodeURIComponent(scenario)}`);
    if (!res.ok) throw new Error();
    const data = await res.json();

    // Courbe / barres 7 jours
    const maxV = Math.max(...data.forecast_7d.map((d) => d.predicted_visitors), 1);
    document.getElementById("pred-chart").innerHTML = data.forecast_7d
      .map((d) => {
        const h = Math.max(12, Math.round((d.predicted_visitors / maxV) * 120));
        return `
          <div class="bar-col" title="Confiance ${d.confidence_pct}%">
            <span class="bar-val">${d.predicted_visitors}</span>
            <div class="bar-fill" style="height:${h}px"></div>
            <span class="bar-day">${escapeHtml(d.day)}</span>
          </div>
        `;
      })
      .join("");

    // Cross-sell
    document.getElementById("pred-cross-sell").innerHTML = data.cross_sell
      .map(
        (c) => `
        <div class="insight-item">
          <div class="meta-row">
            <strong>${escapeHtml(c.trigger)}</strong>
            <span class="badge">${c.probability_pct}% prob.</span>
          </div>
          <div class="product-meta">→ ${escapeHtml(c.recommended)}</div>
        </div>
      `
      )
      .join("");

    // Tableau ruptures
    document.getElementById("pred-stock-body").innerHTML = data.stock_forecasts
      .map((p) => {
        const riskCls =
          p.risk === "critique"
            ? "severity-critique"
            : p.risk === "attention"
            ? "severity-attention"
            : "severity-surveiller";
        return `
          <tr>
            <td><strong>${escapeHtml(p.name)}</strong><br><span class="product-sku">${escapeHtml(p.sku)} · ${escapeHtml(p.aisle)}</span></td>
            <td>${p.stock_qty}</td>
            <td>${p.predicted_daily_sales} / j</td>
            <td><strong>${p.days_until_stockout} j</strong></td>
            <td>${p.recommended_reorder > 0 ? `+${p.recommended_reorder} u.` : "Suffisant"}</td>
            <td><span class="badge ${riskCls}">${escapeHtml(p.risk.toUpperCase())}</span></td>
          </tr>
        `;
      })
      .join("");
  } catch {
    document.getElementById("pred-stock-body").innerHTML =
      `<tr><td colspan="6">Impossible de charger les prédictions.</td></tr>`;
  }
}

document.getElementById("pred-scenario").addEventListener("change", loadPredictions);
document.getElementById("pred-ai-btn").addEventListener("click", async () => {
  const btn = document.getElementById("pred-ai-btn");
  const box = document.getElementById("pred-ai-box");
  const scenario = document.getElementById("pred-scenario").value;
  btn.disabled = true;
  btn.textContent = "Analyse IA…";
  try {
    const res = await fetch(`${API_BASE}/predictions/ai-brief?scenario=${encodeURIComponent(scenario)}`, {
      method: "POST",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erreur");
    document.getElementById("pred-ai-meta").textContent = data.mode;
    document.getElementById("pred-ai-brief").textContent = data.brief;
    box.classList.remove("hidden");
  } catch (err) {
    alert(`Erreur synthèse IA : ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "✨ Synthèse IA (qwen3:8b)";
  }
});

loadPredictions();

// --- Achats / Ventes & Stocks ---
async function loadInventory() {
  try {
    const res = await fetch(`${API_BASE}/inventory-overview`);
    if (!res.ok) throw new Error();
    const data = await res.json();

    // KPIs
    document.getElementById("inv-kpi-stock").textContent = `${data.kpis.total_stock_units} u.`;
    document.getElementById("inv-kpi-value").textContent = `${data.kpis.stock_value_eur.toFixed(2)} €`;
    document.getElementById("inv-kpi-purchases").textContent = `${data.kpis.total_purchases_qty} u.`;
    document.getElementById("inv-kpi-sales").textContent =
      `${data.kpis.total_sales_qty} u. (${data.kpis.sales_revenue_eur.toFixed(0)} €)`;

    // Select SKU (preserve selection if already populated)
    const skuSelect = document.getElementById("inv-sku");
    const prevSku = skuSelect.value;
    skuSelect.innerHTML = data.products
      .map((p) => `<option value="${escapeHtml(p.sku)}">${escapeHtml(p.sku)} — ${escapeHtml(p.name)}</option>`)
      .join("");
    if (prevSku) skuSelect.value = prevSku;

    // Graphique groupé par catégorie
    const maxVal = Math.max(
      ...data.chart_by_category.flatMap((c) => [c.purchases_qty, c.sales_qty, c.stock_qty]),
      1
    );
    document.getElementById("inv-chart").innerHTML = data.chart_by_category
      .map((c) => {
        const hp = Math.max(6, Math.round((c.purchases_qty / maxVal) * 130));
        const hs = Math.max(6, Math.round((c.sales_qty / maxVal) * 130));
        const hk = Math.max(6, Math.round((c.stock_qty / maxVal) * 130));
        return `
          <div class="group-col">
            <div class="group-bars">
              <div class="g-bar g-bar-purchase" style="height:${hp}px" title="Achats : ${c.purchases_qty}"></div>
              <div class="g-bar g-bar-sale" style="height:${hs}px" title="Ventes : ${c.sales_qty}"></div>
              <div class="g-bar g-bar-stock" style="height:${hk}px" title="Stock : ${c.stock_qty}"></div>
            </div>
            <div class="group-label">${escapeHtml(c.category)}<br>(${c.purchases_qty} / ${c.sales_qty} / ${c.stock_qty})</div>
          </div>
        `;
      })
      .join("");

    // Tableau détaillé
    document.getElementById("inv-table-body").innerHTML = data.products
      .map((p) => {
        const badgeCls = p.stock_qty <= 10 ? "severity-critique" : p.stock_qty <= 20 ? "severity-attention" : "severity-surveiller";
        return `
          <tr>
            <td><strong>${escapeHtml(p.name)}</strong><br><span class="product-sku">${escapeHtml(p.sku)} · ${escapeHtml(p.aisle)}</span></td>
            <td>${escapeHtml(p.category)}</td>
            <td>+${p.purchases_qty}</td>
            <td>-${p.sales_qty}</td>
            <td><span class="badge ${badgeCls}">${p.stock_qty}</span></td>
            <td><strong>${p.stock_value.toFixed(2)} €</strong></td>
          </tr>
        `;
      })
      .join("");

    // Derniers mouvements
    document.getElementById("inv-movements").innerHTML = data.recent_movements
      .map((m) => {
        const isPurchase = m.movement_type === "purchase";
        const badge = isPurchase
          ? `<span class="badge">➕ ACHAT +${m.qty}</span>`
          : `<span class="badge warn">➖ VENTE -${m.qty}</span>`;
        return `
          <div class="insight-item">
            <div class="meta-row">
              <strong>${escapeHtml(m.product_name)} (${escapeHtml(m.sku)})</strong>
              ${badge}
            </div>
            <div class="product-meta">
              Prix unitaire : ${m.unit_price.toFixed(2)} € · ${escapeHtml(m.note)}
            </div>
          </div>
        `;
      })
      .join("");
  } catch {
    document.getElementById("inv-table-body").innerHTML =
      `<tr><td colspan="6">Impossible de charger l'inventaire.</td></tr>`;
  }
}

document.getElementById("inv-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("inv-submit");
  btn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/inventory-movement`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sku: document.getElementById("inv-sku").value,
        movement_type: document.getElementById("inv-type").value,
        qty: Number(document.getElementById("inv-qty").value),
        note: document.getElementById("inv-note").value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erreur");
    document.getElementById("inv-note").value = "";
    await Promise.all([loadInventory(), loadCatalog(), loadStockAlerts(), loadPredictions()]);
  } catch (err) {
    alert(`Erreur mouvement : ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

loadInventory();

let invAutoTimer = null;

async function runAutoInventory(mode) {
  const status = document.getElementById("inv-auto-status");
  const res = await fetch(`${API_BASE}/inventory-auto-simulate?mode=${encodeURIComponent(mode)}`, {
    method: "POST",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Erreur");

  const summary = data.actions
    .map((a) => `${a.movement_type === "purchase" ? "+" : "-"}${a.qty} ${a.sku}`)
    .join(", ");
  status.textContent = `Simulation (${mode}) : ${summary || "aucun mouvement"}`;
  status.classList.remove("hidden");
  await Promise.all([loadInventory(), loadCatalog(), loadStockAlerts(), loadPredictions()]);
}

document.getElementById("inv-auto-buy-btn").addEventListener("click", async () => {
  const btn = document.getElementById("inv-auto-buy-btn");
  btn.disabled = true;
  try {
    await runAutoInventory("purchases");
  } catch (err) {
    alert(`Erreur simulation achats : ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("inv-auto-loop-btn").addEventListener("click", () => {
  const btn = document.getElementById("inv-auto-loop-btn");
  if (invAutoTimer) {
    clearInterval(invAutoTimer);
    invAutoTimer = null;
    btn.textContent = "▶ Flux auto (ventes + achats)";
    return;
  }
  btn.textContent = "⏸ Arrêter flux auto";
  runAutoInventory("cycle").catch(() => {});
  invAutoTimer = setInterval(() => {
    runAutoInventory("cycle").catch(() => {});
  }, 3000);
});

// --- Liste d'achats & Parcours multi-rayons (A -> B -> C -> D) ---
const SHOPPING_STORAGE_KEY = "famiflora_shopping_list";
let shoppingList = JSON.parse(localStorage.getItem(SHOPPING_STORAGE_KEY) || "[]");

function saveShoppingList() {
  localStorage.setItem(SHOPPING_STORAGE_KEY, JSON.stringify(shoppingList));
}

function aisleSortKey(aisle) {
  const match = String(aisle || "").match(/\b([A-D])(\d+)\b/i);
  if (!match) return "Z999";
  return `${match[1].toUpperCase()}${match[2].padStart(3, "0")}`;
}

function zoneIdForAisle(aisle) {
  const zone = STORE_ZONES.find((z) => z.prefix && aisle.startsWith(z.prefix));
  return zone ? zone.id : "serre";
}

window.addToShoppingList = function (sku, name, aisle, price) {
  const existing = shoppingList.find((item) => item.sku === sku);
  if (existing) {
    existing.qty += 1;
  } else {
    shoppingList.push({ sku, name, aisle, price: Number(price), qty: 1 });
  }
  saveShoppingList();
  renderShoppingList();
};

window.updateShoppingQty = function (sku, delta) {
  const idx = shoppingList.findIndex((item) => item.sku === sku);
  if (idx === -1) return;
  shoppingList[idx].qty += delta;
  if (shoppingList[idx].qty <= 0) {
    shoppingList.splice(idx, 1);
  }
  saveShoppingList();
  renderShoppingList();
};

function renderShoppingList() {
  const totalQty = shoppingList.reduce((sum, i) => sum + i.qty, 0);
  const totalPrice = shoppingList.reduce((sum, i) => sum + i.price * i.qty, 0);

  document.getElementById("cart-count").textContent = totalQty;
  document.getElementById("shop-total-items").textContent = totalQty;
  document.getElementById("shop-total-price").textContent = `${totalPrice.toFixed(2)} €`;

  const listEl = document.getElementById("shop-items-list");
  const stepsEl = document.getElementById("shop-route-steps");
  const mapEl = document.getElementById("shop-minimap");

  if (shoppingList.length === 0) {
    document.getElementById("shop-est-time").textContent = "0 min";
    listEl.innerHTML = `<p class="product-meta">Votre liste est vide. Cliquez sur <strong>🛒 + Liste</strong> sur un produit pour commencer.</p>`;
    mapEl.innerHTML = STORE_ZONES.map((z) => `<div class="minimap-cell">${escapeHtml(z.label)}</div>`).join("");
    stepsEl.innerHTML = `<li>Ajoutez des articles pour calculer votre parcours optimisé.</li>`;
    return;
  }

  listEl.innerHTML = shoppingList
    .map(
      (item) => `
      <div class="insight-item">
        <div class="meta-row">
          <div>
            <strong>${escapeHtml(item.name)}</strong>
            <div class="product-meta">${escapeHtml(item.sku)} · ${escapeHtml(item.aisle)} · ${item.price.toFixed(2)} €/u.</div>
          </div>
          <div class="qty-controls">
            <button type="button" class="secondary qty-btn" onclick="updateShoppingQty('${escapeHtml(item.sku)}', -1)">−</button>
            <strong>${item.qty}</strong>
            <button type="button" class="secondary qty-btn" onclick="updateShoppingQty('${escapeHtml(item.sku)}', 1)">+</button>
          </div>
        </div>
      </div>
    `
    )
    .join("");

  const ordered = [...shoppingList].sort((a, b) =>
    aisleSortKey(a.aisle).localeCompare(aisleSortKey(b.aisle))
  );
  const visitedZones = new Set(ordered.map((i) => zoneIdForAisle(i.aisle)));
  const estMinutes = Math.max(3, visitedZones.size * 3 + ordered.length);
  document.getElementById("shop-est-time").textContent = `~${estMinutes} min`;

  mapEl.innerHTML = STORE_ZONES.map((z) => {
    const cls = z.id === "entree" || z.id === "caisses" ? "start" : visitedZones.has(z.id) ? "target" : "";
    return `<div class="minimap-cell ${cls}">${escapeHtml(z.label)}</div>`;
  }).join("");

  stepsEl.innerHTML = [
    `<li>Départ depuis l'<strong>Entrée &amp; Accueil</strong>.</li>`,
    ...ordered.map(
      (item) =>
        `<li><strong>${escapeHtml(item.aisle)}</strong> — Prendre <em>${escapeHtml(item.name)}</em> (×${item.qty})</li>`
    ),
    `<li>Terminer aux <strong>Caisses</strong> (total estimé : <strong>${totalPrice.toFixed(2)} €</strong>).</li>`,
  ].join("");
}

document.getElementById("shop-clear-btn").addEventListener("click", () => {
  shoppingList = [];
  saveShoppingList();
  renderShoppingList();
});

renderShoppingList();

// --- Capteurs d'humidité IoT ---
let sensorTimer = null;

async function loadSensors() {
  const grid = document.getElementById("sensors-grid");
  try {
    const res = await fetch(`${API_BASE}/sensors`);
    if (!res.ok) throw new Error();
    const data = await res.json();

    document.getElementById("sens-kpi-total").textContent = data.kpis.total_sensors;
    document.getElementById("sens-kpi-dry").textContent = data.kpis.needs_watering;
    document.getElementById("sens-kpi-soil").textContent = `${data.kpis.avg_soil_moisture}%`;
    document.getElementById("sens-kpi-temp").textContent = `${data.kpis.avg_temperature_c} °C`;

    grid.innerHTML = data.sensors
      .map((s) => {
        const badge = s.needs_watering
          ? `<span class="badge warn">⚠️ Arrosage requis (&lt; ${s.target_min_moisture}%)</span>`
          : `<span class="badge">✅ Optimal</span>`;
        const soilColor = s.needs_watering ? "#c92a2a" : "#2d6a4f";
        const plantsHtml = s.plants.length
          ? s.plants.map((p) => `<span class="tag">${escapeHtml(p.name)}</span>`).join("")
          : `<span class="product-meta">Aucune plante liée</span>`;

        return `
          <article class="product-card">
            <div class="meta-row">
              <span class="product-sku">${escapeHtml(s.sensor_id)}</span>
              ${badge}
            </div>
            <h4>${escapeHtml(s.zone_label)}</h4>

            <div class="meter-wrap">
              <div class="meter-label"><span>💧 Humidité du sol</span><strong>${s.soil_moisture_pct}%</strong></div>
              <div class="meter-bar"><div class="meter-fill" style="width:${s.soil_moisture_pct}%;background:${soilColor};"></div></div>
            </div>

            <div class="meter-wrap">
              <div class="meter-label"><span>🌫️ Humidité de l'air</span><strong>${s.air_humidity_pct}%</strong></div>
              <div class="meter-bar"><div class="meter-fill" style="width:${s.air_humidity_pct}%;background:#2b6cb0;"></div></div>
            </div>

            <div class="product-meta">🌡️ Température : <strong>${s.temperature_c} °C</strong></div>
            <div class="tag-list">${plantsHtml}</div>
            <button type="button" onclick="waterZone('${escapeHtml(s.sensor_id)}')">🚿 Arroser la zone</button>
          </article>
        `;
      })
      .join("");
  } catch {
    grid.innerHTML = `<p class="product-meta">Impossible de charger les capteurs.</p>`;
  }
}

window.waterZone = async function (sensorId) {
  await fetch(`${API_BASE}/sensors/water/${encodeURIComponent(sensorId)}`, { method: "POST" });
  await loadSensors();
};

document.getElementById("sensor-tick-btn").addEventListener("click", async () => {
  await fetch(`${API_BASE}/sensors/simulate-tick`, { method: "POST" });
  await loadSensors();
});

document.getElementById("sensor-live-btn").addEventListener("click", () => {
  const btn = document.getElementById("sensor-live-btn");
  if (sensorTimer) {
    clearInterval(sensorTimer);
    sensorTimer = null;
    btn.textContent = "▶ Flux IoT temps réel";
    return;
  }
  btn.textContent = "⏸ Arrêter flux IoT";
  sensorTimer = setInterval(async () => {
    await fetch(`${API_BASE}/sensors/simulate-tick`, { method: "POST" });
    await loadSensors();
  }, 2500);
});

loadSensors();

// --- Anti-Gaspillage / Démarque intelligente ---
async function loadWasteMarkdowns() {
  const grid = document.getElementById("waste-grid");
  try {
    const res = await fetch(`${API_BASE}/waste-markdowns`);
    if (!res.ok) throw new Error();
    const data = await res.json();

    document.getElementById("waste-kpi-risk").textContent = data.kpis.at_risk_count;
    document.getElementById("waste-kpi-active").textContent = data.kpis.active_promos;
    document.getElementById("waste-kpi-saved").textContent = `${data.kpis.potential_saved_eur.toFixed(2)} €`;

    if (!data.candidates.length) {
      grid.innerHTML = `<p class="product-meta">Aucune plante à risque de perte actuellement.</p>`;
      return;
    }

    grid.innerHTML = data.candidates
      .map((c) => {
        const isActive = c.active_discount_pct > 0;
        const badge = isActive
          ? `<span class="badge">🏷️ Promo -${c.active_discount_pct}% active</span>`
          : `<span class="badge warn">⚠️ À démarquer</span>`;
        const reasons = c.reasons.map((r) => `<span class="tag">${escapeHtml(r)}</span>`).join("");
        const actionBtn = isActive
          ? `<button type="button" class="secondary" onclick="setMarkdown('${escapeHtml(c.sku)}', 0)">✕ Retirer promo</button>`
          : `<button type="button" onclick="setMarkdown('${escapeHtml(c.sku)}', ${c.suggested_discount_pct})">🏷️ Activer Promo Flash (-${c.suggested_discount_pct}%)</button>`;

        return `
          <article class="product-card">
            <div class="meta-row">
              <span class="product-sku">${escapeHtml(c.sku)} · ${escapeHtml(c.aisle)}</span>
              ${badge}
            </div>
            <h4>${escapeHtml(c.name)}</h4>
            <div class="product-price">
              <span style="text-decoration:line-through;color:var(--muted);font-size:0.85rem;margin-right:0.4rem;">${c.original_price.toFixed(2)} €</span>
              ${c.suggested_promo_price.toFixed(2)} €
            </div>
            <div class="product-meta">Stock : <strong>${c.stock_qty}</strong> unités · Couverture : <strong>${c.days_of_stock} j</strong></div>
            <div class="tag-list">${reasons}</div>
            ${actionBtn}
          </article>
        `;
      })
      .join("");
  } catch {
    grid.innerHTML = `<p class="product-meta">Impossible de charger l'analyse anti-gaspillage.</p>`;
  }
}

window.setMarkdown = async function (sku, discountPct) {
  await fetch(`${API_BASE}/waste-markdowns/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sku, discount_pct: discountPct }),
  });
  await Promise.all([loadWasteMarkdowns(), loadCatalog()]);
};

loadWasteMarkdowns();

// --- Monitoring Statistique ---
async function loadMonitoringStats() {
  try {
    const res = await fetch(`${API_BASE}/monitoring/stats`);
    if (!res.ok) throw new Error();
    const data = await res.json();

    const st = data.descriptive.stock;
    document.getElementById("mon-stock-desc").textContent = `${st.mean} / ${st.median} u.`;
    document.getElementById("mon-stock-disp").textContent = `σ=${st.stdev} · IQR=${st.iqr}`;
    document.getElementById("mon-pearson").textContent = `r = ${data.correlation.pearson_r}`;
    document.getElementById("mon-abc-counts").textContent =
      `A:${data.abc_summary.A} · B:${data.abc_summary.B} · C:${data.abc_summary.C}`;

    document.getElementById("mon-ci-list").innerHTML = data.forecast_ci
      .map(
        (d) => `
        <div class="insight-item">
          <div class="meta-row">
            <strong>${escapeHtml(d.day)} — Moyenne : ${d.mean} visiteurs</strong>
            <span class="badge">IC 95% : [${d.ci_low} ; ${d.ci_high}]</span>
          </div>
        </div>
      `
      )
      .join("");

    const anomEl = document.getElementById("mon-anomalies");
    if (!data.anomalies.length) {
      anomEl.innerHTML = `<p class="product-meta">Aucune anomalie statistique détectée.</p>`;
    } else {
      anomEl.innerHTML = data.anomalies
        .map(
          (a) => `
          <div class="insight-item">
            <div class="meta-row">
              <strong>${escapeHtml(a.source)} — ${escapeHtml(a.label)}</strong>
              <span class="badge warn">Z = ${a.z_score} (${escapeHtml(a.direction)})</span>
            </div>
            <div class="product-meta">Valeur observée : ${escapeHtml(a.value)}</div>
          </div>
        `
        )
        .join("");
    }

    document.getElementById("mon-abc-body").innerHTML = data.abc_items
      .map((item) => {
        const ropBadge = item.below_rop
          ? `<span class="badge warn">${item.reorder_point} u. (Sous seuil)</span>`
          : `<span class="badge">${item.reorder_point} u.</span>`;
        return `
          <tr>
            <td><strong>${escapeHtml(item.name)}</strong><br><span class="product-sku">${escapeHtml(item.sku)}</span></td>
            <td><span class="badge">${escapeHtml(item.abc_class)}</span></td>
            <td>${item.cum_revenue_pct}% (${item.sales_revenue.toFixed(0)} €)</td>
            <td>${item.stock_qty}</td>
            <td>${item.safety_stock} u.</td>
            <td>${ropBadge}</td>
          </tr>
        `;
      })
      .join("");
  } catch {
    document.getElementById("mon-abc-body").innerHTML =
      `<tr><td colspan="6">Impossible de charger les statistiques.</td></tr>`;
  }
}

loadMonitoringStats();

// --- Automatisations ---
async function loadAutomations() {
  try {
    const res = await fetch(`${API_BASE}/automations`);
    if (!res.ok) throw new Error();
    const data = await res.json();

    document.getElementById("auto-rules-grid").innerHTML = data.rules
      .map(
        (r) => `
        <article class="product-card">
          <div class="meta-row">
            <span class="product-sku">${escapeHtml(r.id)}</span>
            <span class="badge">${escapeHtml(r.trigger)}</span>
          </div>
          <h4>${escapeHtml(r.name)}</h4>
          <p class="product-meta" style="margin:0;">${escapeHtml(r.description)}</p>
          <button type="button" class="secondary" style="margin-top:auto;" onclick="runAutomationRule('${escapeHtml(r.id)}', this)">▶ Exécuter</button>
        </article>
      `
      )
      .join("");

    const logsEl = document.getElementById("auto-logs-list");
    if (!data.logs.length) {
      logsEl.innerHTML = `<p class="product-meta">Aucune automatisation exécutée pour le moment.</p>`;
    } else {
      logsEl.innerHTML = data.logs
        .map(
          (l) => `
          <div class="insight-item">
            <div class="meta-row">
              <strong>${escapeHtml(l.rule_name)}</strong>
              <span class="badge">${escapeHtml(l.trigger_type)} · ${escapeHtml(l.status)}</span>
            </div>
            <div class="product-meta" style="white-space:pre-wrap;">${escapeHtml(l.summary)}</div>
          </div>
        `
        )
        .join("");
    }
  } catch {
    document.getElementById("auto-rules-grid").innerHTML =
      `<p class="product-meta">Impossible de charger les automatisations.</p>`;
  }
}

window.runAutomationRule = async function (ruleId, btn) {
  if (btn) btn.disabled = true;
  try {
    await fetch(`${API_BASE}/automations/run/${encodeURIComponent(ruleId)}`, { method: "POST" });
    await Promise.all([
      loadAutomations(),
      loadInventory(),
      loadCatalog(),
      loadSensors(),
      loadWasteMarkdowns(),
      loadMonitoringStats(),
    ]);
  } finally {
    if (btn) btn.disabled = false;
  }
};

document.getElementById("auto-run-all-btn").addEventListener("click", async () => {
  const btn = document.getElementById("auto-run-all-btn");
  btn.disabled = true;
  btn.textContent = "Exécution…";
  try {
    await fetch(`${API_BASE}/automations/run-all`, { method: "POST" });
    await Promise.all([
      loadAutomations(),
      loadInventory(),
      loadCatalog(),
      loadSensors(),
      loadWasteMarkdowns(),
      loadMonitoringStats(),
    ]);
  } finally {
    btn.disabled = false;
    btn.textContent = "⚡ Tout exécuter";
  }
});

loadAutomations();
