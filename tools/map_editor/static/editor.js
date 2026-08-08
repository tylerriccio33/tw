// The map editor UI.
//
// Nothing here names a layer. The sidebar, the tools, the snap targets
// and the export are all built from /api/manifest, so adding a layer to
// map.json makes it appear with its own row, its own legend and its own
// editing gesture without a line changing in this file.
//
// Three gestures, chosen per layer by its `input`:
//   polygon  trace rings with snapping and magnetic trace (coastline, provinces)
//   brush    paint a raster (terrain, resources)
//   assign   click a province to give it a key (ownership)

const SNAP_SCREEN_PX = 14; // snap radius in screen px, so it feels the same at any zoom
const SNAP_CELL_PX = 32; // spatial hash cell for snap candidates
const TRACE_EPSILON_SCREEN = 0.75; // Douglas-Peucker tolerance, in screen px
const MAX_TRACE_VERTICES = 600;
const PAN_SPEED = 1000; // px/s
const ZOOM_SPEED = 1.2; // e-folds/s
const AUTOSAVE_MS = 1500;

const state = {
  manifest: null,
  project: null,
  activeLayer: null,
  visible: {},
  referenceVisible: false,
  magnet: {},
  selected: {}, // layer -> feature index (identity) or legend key (others)
  drawing: null, // points of the ring being placed
  editMode: false,
  tool: "draw", // draw | trace | brush | bucket | eraser
  traceAnchor: null,
  tracePreview: null,
  brushSize: 24,
  zoom: 1,
  snapIndex: null,
  painting: false,
  dirtyRasters: new Set(),
};

const el = {};
const canvases = {}; // layer name -> <canvas> for brush layers

// ---------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------

function setStatus(message, isError) {
  el.status.textContent = message || "";
  el.status.classList.toggle("error", !!isError);
}

const layerCfg = (name) => state.manifest.layers[name];
const activeCfg = () => (state.activeLayer ? layerCfg(state.activeLayer) : null);

function features(name) {
  const layer = state.project.layers[name];
  if (!layer.features) layer.features = [];
  return layer.features;
}

function assignments(name) {
  const layer = state.project.layers[name];
  if (!layer.assignments) layer.assignments = {};
  return layer.assignments;
}

function points(name) {
  const layer = state.project.layers[name];
  if (!layer.points) layer.points = {};
  return layer.points;
}

// Free-point layers (army starts, ...) aren't coupled to a province, so
// each point needs its own generated id.
function nextPointId(name) {
  const existing = points(name);
  let n = 1;
  while (existing[`p${n}`]) n++;
  return `p${n}`;
}

function defaultPointPayload(cfg) {
  const payload = {};
  for (const [fieldName, fieldCfg] of Object.entries(cfg.point_fields || {})) {
    if (fieldCfg.type === "faction") {
      payload[fieldName] = state.manifest.factions?.[0]?.key ?? null;
    } else if (fieldCfg.type === "counts") {
      payload[fieldName] = Object.fromEntries((fieldCfg.keys || []).map((k) => [k, 0]));
    } else if (fieldCfg.type === "tier") {
      payload[fieldName] = fieldCfg.min ?? 1;
    } else if (fieldCfg.type === "name") {
      payload[fieldName] = `New City ${Object.keys(points(cfg.name)).length + 1}`;
    }
  }
  return payload;
}

// Whichever payload field labels a free-point layer's dots with text
// (cities' "name" field). Mirrors colorFieldName below.
function nameFieldName(cfg) {
  const found = Object.entries(cfg.point_fields || {}).find(([, fc]) => fc.type === "name");
  return found ? found[0] : null;
}

// Whichever payload field colors a free-point layer's dots: "faction" if
// it has one (army starts), otherwise "tier" (cities). Mirrors
// export.py's _color_field_name.
function colorFieldName(cfg) {
  for (const wanted of ["faction", "tier"]) {
    const found = Object.entries(cfg.point_fields || {}).find(([, fc]) => fc.type === wanted);
    if (found) return found[0];
  }
  return null;
}

function legendEntries(cfg) {
  return Object.entries(cfg.legend || {}).map(([color, entry]) => ({
    color,
    key: entry.key,
    name: entry.name || entry.key,
  }));
}

function colorForKey(cfg, key) {
  const found = legendEntries(cfg).find((entry) => entry.key === key);
  return found ? found.color : "#808080";
}

function darken(hex, amount = 0.45) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * (1 - amount));
  const g = Math.round(((n >> 8) & 255) * (1 - amount));
  const b = Math.round((n & 255) * (1 - amount));
  return `rgb(${r},${g},${b})`;
}

function slugify(name) {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

const PALETTE = [
  "#4363d8",
  "#f58231",
  "#911eb4",
  "#46f0f0",
  "#bfef45",
  "#fabed4",
  "#469990",
  "#dcbeff",
  "#9a6324",
  "#808000",
  "#ffd8b1",
  "#000075",
  "#c65102",
  "#c50202",
  "#c5ab02",
  "#3cb44b",
  "#e6194b",
  "#42d4f4",
];

// Native prompt/confirm read as "the button did nothing" when this is
// embedded in a webview, so dialogs are in-page.
function showModal(title, inputValue, onOk) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const hasInput = inputValue !== null && inputValue !== undefined;
  overlay.innerHTML = `
    <div class="modal-box">
      <div class="modal-title">${title}</div>
      ${hasInput ? '<input class="modal-input" type="text">' : ""}
      <div class="modal-actions">
        <button class="cancel">Cancel</button>
        <button class="ok primary">OK</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  /** @type {HTMLInputElement | null} */
  const input = overlay.querySelector(".modal-input");
  const cancelBtn = /** @type {HTMLElement} */ (overlay.querySelector(".cancel"));
  const okBtn = /** @type {HTMLElement} */ (overlay.querySelector(".ok"));
  if (input) {
    input.value = inputValue;
    input.focus();
    input.select();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") okBtn.click();
      if (e.key === "Escape") cancelBtn.click();
    });
  }
  cancelBtn.onclick = () => overlay.remove();
  okBtn.onclick = () => {
    overlay.remove();
    onOk(input ? input.value : true);
  };
}

const showPrompt = (title, value, cb) => showModal(title, value ?? "", cb);
const showConfirm = (title, cb) => showModal(title, null, cb);

// ---------------------------------------------------------------------------
// loading
// ---------------------------------------------------------------------------

async function boot() {
  el.layerList = document.getElementById("layerList");
  el.featureList = document.getElementById("featureList");
  el.tools = document.getElementById("tools");
  el.status = document.getElementById("status");
  el.stage = document.getElementById("stage");
  el.backdrop = document.getElementById("bgImage");
  el.reference = document.getElementById("referenceImage");
  el.referenceRow = document.getElementById("referenceRow");
  el.referenceToggle = document.getElementById("referenceToggle");
  el.overlay = document.getElementById("overlay");
  el.hint = document.getElementById("hint");
  el.viewport = document.getElementById("viewport");

  state.manifest = await (await fetch("/api/manifest")).json();
  if (state.manifest.error) {
    setStatus(state.manifest.error, true);
    return;
  }
  state.project = await (await fetch("/api/project")).json();

  const [width, height] = state.manifest.size;
  el.backdrop.src = "/api/backdrop";
  el.overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
  el.overlay.setAttribute("preserveAspectRatio", "none");

  if (state.manifest.has_reference) {
    el.referenceRow.style.display = "flex";
    el.reference.src = "/api/reference";
    el.referenceToggle.onclick = () => {
      state.referenceVisible = !state.referenceVisible;
      el.reference.style.display = state.referenceVisible ? "block" : "none";
      el.referenceToggle.textContent = state.referenceVisible ? "◉" : "○";
      el.referenceToggle.classList.toggle("on", state.referenceVisible);
    };
  }

  for (const name of state.manifest.layer_order) {
    state.visible[name] = true;
    const cfg = layerCfg(name);
    if (cfg.input === "brush") await mountBrushCanvas(name);
    state.selected[name] =
      cfg.input === "polygon" && cfg.kind === "identity"
        ? -1
        : cfg.default_key || (legendEntries(cfg)[0] || {}).key || null;
  }

  setActiveLayer(state.project.active_layer || state.manifest.layer_order[0]);
  setZoom(1);
  bindEvents();
  render();
}

async function mountBrushCanvas(name) {
  const [width, height] = state.manifest.size;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.className = "layer-canvas";
  canvas.dataset.layer = name;
  el.stage.insertBefore(canvas, el.overlay);
  canvases[name] = canvas;

  const image = new Image();
  await new Promise((resolve) => {
    image.onload = resolve;
    image.onerror = resolve;
    image.src = `/api/layer/${name}.png?t=${Date.now()}`;
  });
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(image, 0, 0);
  makeNodataTransparent(canvas, layerCfg(name).nodata_color);
}

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// nodata is the layer's "nothing here" color. On screen that has to be
// transparent, or every layer above the coastline would hide the ones
// below it.
function makeNodataTransparent(canvas, nodataHex) {
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const [nr, ng, nb] = hexToRgb(nodataHex);
  const data = image.data;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] === nr && data[i + 1] === ng && data[i + 2] === nb) data[i + 3] = 0;
  }
  ctx.putImageData(image, 0, 0);
}

// ---------------------------------------------------------------------------
// layer + feature lists
// ---------------------------------------------------------------------------

function setActiveLayer(name) {
  state.activeLayer = name;
  state.project.active_layer = name;
  state.drawing = null;
  state.editMode = false;
  state.traceAnchor = null;
  state.tracePreview = null;
  state.snapIndex = null;

  // Default the magnets to everything already drawn underneath - which is
  // the reason manifest order is what it is.
  const cfg = layerCfg(name);
  for (const other of state.manifest.layer_order) state.magnet[other] = false;
  for (const other of cfg.snap_candidates) state.magnet[other] = true;

  state.tool = cfg.input === "brush" ? "brush" : "draw";
  renderSidebar();
  render();
}

function renderSidebar() {
  renderLayerList();
  renderTools();
  renderFeatureList();
  renderHint();
}

function renderLayerList() {
  el.layerList.innerHTML = "";
  for (const name of state.manifest.layer_order) {
    const cfg = layerCfg(name);
    const row = document.createElement("div");
    row.className = "layer-row" + (name === state.activeLayer ? " active" : "");

    const title = document.createElement("span");
    title.className = "layer-title";
    title.textContent = cfg.title;
    title.onclick = () => setActiveLayer(name);

    const badge = document.createElement("span");
    badge.className = "layer-badge";
    badge.textContent = cfg.input;

    const eye = document.createElement("button");
    eye.className = "icon" + (state.visible[name] ? " on" : "");
    eye.textContent = state.visible[name] ? "◉" : "○";
    eye.title = "Show this layer";
    eye.onclick = (e) => {
      e.stopPropagation();
      state.visible[name] = !state.visible[name];
      renderLayerList();
      render();
    };

    const magnet = document.createElement("button");
    const canSnap = cfg.snap_source && name !== state.activeLayer;
    magnet.className = "icon" + (state.magnet[name] ? " on" : "");
    magnet.textContent = "⌁";
    magnet.title = canSnap ? "Snap to this layer" : "Not a snap source";
    magnet.disabled = !canSnap;
    magnet.onclick = (e) => {
      e.stopPropagation();
      state.magnet[name] = !state.magnet[name];
      state.snapIndex = null;
      renderLayerList();
      render();
    };

    row.append(title, badge, eye, magnet);
    el.layerList.appendChild(row);
  }
}

function renderTools() {
  const cfg = activeCfg();
  el.tools.innerHTML = "";
  if (!cfg) return;

  const addButton = (label, onClick, active, disabled) => {
    const button = document.createElement("button");
    button.textContent = label;
    button.className = active ? "active" : "";
    button.disabled = !!disabled;
    button.onclick = onClick;
    el.tools.appendChild(button);
  };

  if (cfg.input === "polygon") {
    // The province layer is grown from city points (see renderGrowPanel)
    // rather than hand-traced, so it doesn't get the manual drawing tools.
    const isProvinceLayer = state.activeLayer === state.manifest.province_layer;
    if (!isProvinceLayer) {
      if (cfg.kind === "identity") addButton("+ New Province", newIdentityFeature);
      addButton("+ New Shape", newShape);
      addButton(
        state.editMode ? "Edit Vertices: On" : "Edit Vertices: Off",
        () => {
          state.editMode = !state.editMode;
          state.drawing = null;
          renderTools();
          render();
        },
        state.editMode,
      );
      addButton(
        state.tool === "trace" ? "Trace: On (T)" : "Trace (T)",
        toggleTrace,
        state.tool === "trace",
      );
    }
    if (cfg.kind === "mask") addButton("Autotrace from backdrop", runAutotrace);
    if (cfg.gapfill || cfg.clip_to) addButton("Fill Gaps", runFillGaps);
    addButton("Fix Crossings & Overlaps", runCleanShapes);
    if (isProvinceLayer) renderGrowPanel();
  } else if (cfg.input === "brush") {
    addButton("Brush", () => setTool("brush"), state.tool === "brush");
    addButton("Bucket", () => setTool("bucket"), state.tool === "bucket");
    addButton("Eraser", () => setTool("eraser"), state.tool === "eraser");
    if (cfg.default_key) addButton("Reset Layer", resetLayerToDefault);

    const wrap = document.createElement("label");
    wrap.className = "slider";
    const label = document.createElement("span");
    label.textContent = `Size ${state.brushSize}px`;
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "2";
    slider.max = "120";
    slider.value = String(state.brushSize);
    slider.oninput = () => {
      state.brushSize = Number(slider.value);
      label.textContent = `Size ${state.brushSize}px`;
    };
    wrap.append(label, slider);
    el.tools.appendChild(wrap);
  } else if (cfg.input === "point" && cfg.point_coupling === "free") {
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.textContent =
      "Click the map to place a new point. Click an existing point to " +
      "select and edit it below, drag to move it.";
    el.tools.appendChild(hint);
  } else if (cfg.input === "point") {
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.textContent = "Pick a province on the left, then click the map to place its point.";
    el.tools.appendChild(hint);
  } else if (cfg.input === "assign") {
    addButton("+ Add owner", addOwner);
    addButton(
      "Reset all",
      () => {
        const count = Object.keys(assignments(state.activeLayer)).length;
        if (!count) return;
        showConfirm(`Clear ${count} starting owner assignment(s)?`, () => {
          state.project.layers[state.activeLayer].assignments = {};
          scheduleAutosave();
          renderSidebar();
          render();
        });
      },
      false,
      !Object.keys(assignments(state.activeLayer)).length,
    );
  }
}

function renderGrowPanel() {
  const cityLayerName = state.manifest.city_layer;
  const cityCfg = cityLayerName ? layerCfg(cityLayerName) : null;
  if (!cityCfg || cityCfg.point_coupling !== "free") return; // nothing to grow from

  const wrap = document.createElement("div");
  wrap.className = "grow-panel";

  const heading = document.createElement("div");
  heading.className = "section-label";
  heading.textContent = "Grow from cities";
  wrap.appendChild(heading);

  const growth = state.project.layers[state.manifest.province_layer]?.growth;
  const info = document.createElement("div");
  info.className = "hint";
  info.textContent = growth?.seed_of
    ? `Step ${growth.step ?? 0} - ${Object.keys(growth.seed_of).length} city(ies) seeded.`
    : "Not started - place tiered cities, then Start Over to seed provinces.";
  wrap.appendChild(info);

  const row = document.createElement("div");
  row.className = "grow-buttons";
  const startBtn = document.createElement("button");
  startBtn.textContent = "Start Over";
  startBtn.onclick = runGrowStart;
  const stepBtn = document.createElement("button");
  stepBtn.textContent = "Step";
  stepBtn.className = "primary";
  stepBtn.disabled = !growth?.seed_of;
  stepBtn.onclick = runGrowStep;
  row.append(startBtn, stepBtn);
  wrap.appendChild(row);

  el.tools.appendChild(wrap);
}

function setTool(tool) {
  state.tool = tool;
  state.traceAnchor = null;
  state.tracePreview = null;
  renderTools();
  render();
}

function toggleTrace() {
  setTool(state.tool === "trace" ? "draw" : "trace");
}

function currentFeature() {
  const cfg = activeCfg();
  if (!cfg || cfg.input !== "polygon") return null;
  if (cfg.kind === "identity") {
    const index = state.selected[state.activeLayer];
    const list = features(state.activeLayer);
    return index >= 0 && index < list.length ? list[index] : null;
  }
  // A mask layer has one feature per legend key; drawing adds rings to it.
  const key = state.selected[state.activeLayer];
  const list = features(state.activeLayer);
  let found = list.find((f) => f.key === key);
  if (!found && key) {
    found = { key, polygons: [] };
    list.push(found);
  }
  return found || null;
}

function renderFeatureList() {
  const cfg = activeCfg();
  el.featureList.innerHTML = "";
  if (!cfg) return;

  if (cfg.input === "polygon" && cfg.kind === "identity") {
    features(state.activeLayer).forEach((feature, index) => {
      el.featureList.appendChild(identityRow(feature, index));
    });
    if (!features(state.activeLayer).length) {
      const empty = document.createElement("div");
      empty.className = "hint";
      empty.textContent = "No provinces yet - add one to start tracing.";
      el.featureList.appendChild(empty);
    }
    return;
  }

  if (cfg.input === "point" && cfg.point_coupling === "free") {
    renderFreePointList(cfg);
    return;
  }

  if (cfg.input === "point") {
    // Coupled to the province layer: pick a province here, then click the
    // map to place (or replace) that province's point.
    const provincePts = points(state.activeLayer);
    for (const feature of features(state.manifest.province_layer)) {
      const row = document.createElement("div");
      const isActive = String(feature.id) === String(state.selected[state.activeLayer]);
      row.className = "feature-row" + (isActive ? " active" : "");
      const has = provincePts[String(feature.id)] ? " ✓" : "";
      row.textContent = (feature.name || `Province ${feature.id}`) + has;
      row.onclick = () => {
        state.selected[state.activeLayer] = feature.id;
        renderFeatureList();
        render();
      };
      el.featureList.appendChild(row);
    }
    return;
  }

  // Every other layer draws its rows straight from the legend: you paint
  // or assign into fixed categories rather than inventing them.
  for (const entry of legendEntries(cfg)) {
    if (cfg.kind === "mask" && entry.color === cfg.nodata_color) {
      continue; // that key means "nothing drawn here", not something to draw
    }
    el.featureList.appendChild(legendRow(cfg, entry));
  }
}

function renderFreePointList(cfg) {
  const name = cfg.name;
  const pts = points(name);
  const ids = Object.keys(pts).sort();

  if (!ids.length) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = "No points yet - click the map to place one.";
    el.featureList.appendChild(empty);
    return;
  }

  for (const pointId of ids) {
    const payload = pts[pointId];
    const row = document.createElement("div");
    const isActive = String(pointId) === String(state.selected[name]);
    row.className = "feature-row" + (isActive ? " active" : "");
    const nameField = nameFieldName(cfg);
    const colorField = colorFieldName(cfg);
    const label = nameField ? payload[nameField] : colorField ? payload[colorField] : null;
    row.textContent = `${pointId}${label !== null && label !== undefined && label !== "" ? " - " + label : ""}`;
    row.onclick = () => {
      state.selected[name] = pointId;
      renderFeatureList();
      render();
    };
    el.featureList.appendChild(row);
    if (isActive) el.featureList.appendChild(freePointForm(cfg, pointId, payload));
  }
}

function freePointForm(cfg, pointId, payload) {
  const form = document.createElement("div");
  form.className = "point-form";

  for (const [fieldName, fieldCfg] of Object.entries(cfg.point_fields || {})) {
    if (fieldCfg.type === "name") {
      const label = document.createElement("label");
      label.textContent = fieldName;
      const input = document.createElement("input");
      input.type = "text";
      input.value = payload[fieldName] ?? "";
      input.oninput = () => {
        // Rebuilding the sidebar/map on every keystroke replaces this
        // input and drops focus after one character - update the data
        // live, but only re-render the list row and map label on blur.
        payload[fieldName] = input.value;
        scheduleAutosave();
      };
      input.onchange = () => {
        renderFeatureList();
        render();
      };
      label.appendChild(input);
      form.appendChild(label);
    } else if (fieldCfg.type === "faction") {
      const label = document.createElement("label");
      label.textContent = fieldName;
      const select = document.createElement("select");
      for (const faction of state.manifest.factions || []) {
        const option = document.createElement("option");
        option.value = faction.key;
        option.textContent = faction.name || faction.key;
        if (payload[fieldName] === faction.key) option.selected = true;
        select.appendChild(option);
      }
      select.onchange = () => {
        payload[fieldName] = select.value;
        scheduleAutosave();
        render();
      };
      label.appendChild(select);
      form.appendChild(label);
    } else if (fieldCfg.type === "counts") {
      if (!payload[fieldName]) payload[fieldName] = {};
      for (const countKey of fieldCfg.keys || []) {
        const label = document.createElement("label");
        label.textContent = countKey;
        const input = document.createElement("input");
        input.type = "number";
        input.min = fieldCfg.min ?? 0;
        input.value = payload[fieldName][countKey] ?? 0;
        input.oninput = () => {
          payload[fieldName][countKey] = Math.max(fieldCfg.min ?? 0, Number(input.value) || 0);
          scheduleAutosave();
        };
        label.appendChild(input);
        form.appendChild(label);
      }
    } else if (fieldCfg.type === "tier") {
      const label = document.createElement("label");
      label.textContent = fieldName;
      const select = document.createElement("select");
      const lo = fieldCfg.min ?? 1;
      const hi = fieldCfg.max ?? 5;
      for (let t = lo; t <= hi; t++) {
        const option = document.createElement("option");
        option.value = String(t);
        option.textContent = `Tier ${t}`;
        if (payload[fieldName] === t) option.selected = true;
        select.appendChild(option);
      }
      select.onchange = () => {
        payload[fieldName] = Number(select.value);
        scheduleAutosave();
        render();
      };
      label.appendChild(select);
      form.appendChild(label);
    }
  }

  const deleteBtn = document.createElement("button");
  deleteBtn.textContent = "Delete point";
  deleteBtn.onclick = () => {
    delete points(cfg.name)[pointId];
    if (String(state.selected[cfg.name]) === String(pointId)) {
      delete state.selected[cfg.name];
    }
    scheduleAutosave();
    renderFeatureList();
    render();
  };
  form.appendChild(deleteBtn);

  return form;
}

function identityRow(feature, index) {
  const row = document.createElement("div");
  const isActive = index === state.selected[state.activeLayer];
  row.className = "feature-row" + (isActive ? " active" : "");
  row.onclick = () => {
    state.selected[state.activeLayer] = index;
    state.drawing = null;
    renderFeatureList();
    render();
  };

  const swatch = document.createElement("input");
  swatch.type = "color";
  swatch.value = feature.color || "#808080";
  swatch.onclick = (e) => e.stopPropagation();
  swatch.oninput = () => {
    feature.color = swatch.value;
    scheduleAutosave();
    render();
  };

  const name = document.createElement("input");
  name.type = "text";
  name.value = feature.name || "";
  name.onclick = (e) => e.stopPropagation();
  name.onchange = () => {
    feature.name = name.value;
    feature.key = slugify(name.value);
    scheduleAutosave();
  };

  const count = document.createElement("span");
  count.className = "shape-count";
  count.textContent = `${(feature.polygons || []).length}▲`;

  const remove = document.createElement("button");
  remove.className = "del";
  remove.textContent = "✕";
  remove.onclick = (e) => {
    e.stopPropagation();
    showConfirm(`Delete "${feature.name}"?`, () => {
      features(state.activeLayer).splice(index, 1);
      state.selected[state.activeLayer] = -1;
      state.snapIndex = null;
      scheduleAutosave();
      renderSidebar();
      render();
    });
  };

  row.append(swatch, name, count, remove);
  return row;
}

function legendRow(cfg, entry) {
  const row = document.createElement("div");
  const isActive = state.selected[state.activeLayer] === entry.key;
  row.className = "feature-row" + (isActive ? " active" : "");
  row.onclick = () => {
    state.selected[state.activeLayer] = entry.key;
    renderFeatureList();
    render();
  };

  const swatch = document.createElement("span");
  swatch.className = "swatch";
  swatch.style.background = entry.color;

  const name = document.createElement("span");
  name.className = "legend-name";
  name.textContent = entry.name;

  row.append(swatch, name);

  if (cfg.input === "assign") {
    const count = document.createElement("span");
    count.className = "shape-count";
    count.textContent = String(
      Object.values(assignments(state.activeLayer)).filter((k) => k === entry.key).length,
    );
    row.appendChild(count);

    const remove = document.createElement("button");
    remove.className = "del";
    remove.textContent = "✕";
    remove.title = "Delete this starting owner";
    remove.onclick = (e) => {
      e.stopPropagation();
      removeOwner(entry.key);
    };
    row.appendChild(remove);
  }
  return row;
}

// ---------------------------------------------------------------------------
// starting owners (factions)
//
// A faction is the roster entry; an "assign" layer's legend is always
// that same roster read back as paint-by-province categories. Adding or
// deleting one here is the only way to add or delete a starting owner,
// so both go through the server together - it keeps every assign layer's
// legend and every province's assignment in lockstep.
// ---------------------------------------------------------------------------

function addOwner() {
  showPrompt("New owner's name", "", async (name) => {
    if (!name || !name.trim()) return;
    const factions = state.manifest.factions;
    let key = slugify(name);
    let suffix = 2;
    const existingKeys = new Set(factions.map((f) => f.key));
    while (existingKeys.has(key)) key = `${slugify(name)}_${suffix++}`;
    const usedColors = new Set(factions.map((f) => f.color.toLowerCase()));
    const color = PALETTE.find((c) => !usedColors.has(c.toLowerCase())) || PALETTE[0];
    await saveFactions([...factions, { key, name: name.trim(), color, money: 100 }]);
  });
}

function removeOwner(key) {
  const factions = state.manifest.factions;
  if (factions.length <= 1) {
    setStatus("At least one starting owner has to remain", true);
    return;
  }
  const faction = factions.find((f) => f.key === key);
  showConfirm(
    `Delete owner "${faction ? faction.name : key}"? Provinces assigned to ` + "it start unowned.",
    async () => {
      await saveFactions(factions.filter((f) => f.key !== key));
    },
  );
}

async function saveFactions(factions) {
  await flushRasters();
  const response = await fetch("/api/factions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ factions }),
  });
  const result = await response.json();
  if (!result.ok) {
    setStatus(result.error || "Couldn't save owners", true);
    return;
  }
  state.manifest = await (await fetch("/api/manifest")).json();
  state.project = await (await fetch("/api/project")).json();
  for (const name of state.manifest.layer_order) {
    const cfg = layerCfg(name);
    if (cfg.input === "assign" && !legendEntries(cfg).some((e) => e.key === state.selected[name])) {
      state.selected[name] = (legendEntries(cfg)[0] || {}).key || null;
    }
  }
  setStatus("Saved");
  renderSidebar();
  render();
}

function renderHint() {
  const cfg = activeCfg();
  if (!cfg) return;
  const camera = "<b>WASD</b> pan, <b>Z/X</b> zoom, Ctrl/Cmd+wheel zooms.";
  if (cfg.input === "polygon") {
    el.hint.innerHTML =
      "Click to place points. <b>Enter</b>/dbl-click closes a shape, " +
      "<b>Backspace</b> undoes a point, <b>Esc</b> cancels. " +
      "<b>T</b> traces along a snapped boundary: click once to lock on, " +
      "again to take the stretch (hold <b>Alt</b> for the long way round). " +
      camera;
  } else if (cfg.input === "brush") {
    el.hint.innerHTML =
      `Paint into the selected category. Strokes are clipped to ` +
      `<b>${cfg.clip_to || "the map"}</b> on export. ` +
      camera;
  } else {
    el.hint.innerHTML =
      "Pick a category, then click a province to assign it. This is the " +
      "map's <i>starting</i> state - the game owns it from turn one. " +
      camera;
  }
}

function newIdentityFeature() {
  const list = features(state.activeLayer);
  const nextId = list.reduce((max, f) => Math.max(max, f.id || 0), 0) + 1;
  showPrompt("Province name", `Province ${nextId}`, (name) => {
    if (!name.trim()) return;
    list.push({
      id: nextId,
      key: slugify(name),
      name: name.trim(),
      color: PALETTE[list.length % PALETTE.length],
      polygons: [],
    });
    state.selected[state.activeLayer] = list.length - 1;
    state.drawing = [];
    scheduleAutosave();
    renderSidebar();
    render();
  });
}

function newShape() {
  if (!currentFeature()) {
    setStatus("Select or create something to draw into first", true);
    return;
  }
  state.drawing = [];
  state.editMode = false;
  renderSidebar();
  render();
}

// ---------------------------------------------------------------------------
// snapping
// ---------------------------------------------------------------------------

// Rebuilt whenever geometry or the magnet set changes. A flat scan over
// every edge was fine with one layer of regions; with a coastline plus
// provinces plus an in-progress ring it isn't, so segments go into a
// uniform grid and a lookup only touches nearby cells.
function buildSnapIndex() {
  const polylines = [];
  const cells = new Map();

  const addPolyline = (points, closed) => {
    if (points.length < 2) return;
    const index = polylines.length;
    polylines.push(Trace.buildPolyline(points, closed));
    const segments = closed ? points.length : points.length - 1;
    for (let i = 0; i < segments; i++) {
      const a = points[i];
      const b = points[(i + 1) % points.length];
      for (const cell of cellsForSegment(a, b)) {
        if (!cells.has(cell)) cells.set(cell, []);
        cells.get(cell).push({ line: index, seg: i });
      }
    }
  };

  for (const name of state.manifest.layer_order) {
    if (!state.magnet[name] || layerCfg(name).input !== "polygon") continue;
    for (const feature of features(name)) {
      for (const polygon of feature.polygons || []) addPolyline(polygon, true);
    }
  }
  // The ring being drawn is its own snap target, so a border can close
  // cleanly back onto its own start.
  if (state.drawing && state.drawing.length >= 2) addPolyline(state.drawing, false);

  state.snapIndex = { polylines, cells };
}

const cellKey = (cx, cy) => cx + "," + cy;

function cellsForSegment(a, b) {
  const out = [];
  const minX = Math.floor(Math.min(a[0], b[0]) / SNAP_CELL_PX);
  const maxX = Math.floor(Math.max(a[0], b[0]) / SNAP_CELL_PX);
  const minY = Math.floor(Math.min(a[1], b[1]) / SNAP_CELL_PX);
  const maxY = Math.floor(Math.max(a[1], b[1]) / SNAP_CELL_PX);
  for (let cx = minX; cx <= maxX; cx++) {
    for (let cy = minY; cy <= maxY; cy++) out.push(cellKey(cx, cy));
  }
  return out;
}

function snapPoint(x, y) {
  if (!state.snapIndex) buildSnapIndex();
  const radius = SNAP_SCREEN_PX / state.zoom;
  const { polylines, cells } = state.snapIndex;

  const reach = Math.ceil(radius / SNAP_CELL_PX);
  const cx = Math.floor(x / SNAP_CELL_PX);
  const cy = Math.floor(y / SNAP_CELL_PX);

  let best = null;
  let bestDist = radius;
  const seen = new Set();

  for (let ix = cx - reach; ix <= cx + reach; ix++) {
    for (let iy = cy - reach; iy <= cy + reach; iy++) {
      for (const ref of cells.get(cellKey(ix, iy)) || []) {
        const id = ref.line + ":" + ref.seg;
        if (seen.has(id)) continue;
        seen.add(id);

        const polyline = polylines[ref.line];
        const a = polyline.points[ref.seg];
        const b = polyline.points[(ref.seg + 1) % polyline.points.length];

        // Vertices are checked first and win ties, because landing exactly
        // on a neighbour's corner is nearly always what was meant.
        for (const vertex of [a, b]) {
          const d = Math.hypot(x - vertex[0], y - vertex[1]);
          if (d < bestDist) {
            bestDist = d;
            best = [vertex[0], vertex[1]];
          }
        }
        const hit = Trace.closestPointOnSegment(x, y, a, b);
        const d = Math.hypot(x - hit.point[0], y - hit.point[1]);
        if (d < bestDist) {
          bestDist = d;
          best = hit.point;
        }
      }
    }
  }
  return best || [x, y];
}

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

function render() {
  const svg = el.overlay;
  svg.innerHTML = "";

  for (const name of state.manifest.layer_order) {
    const cfg = layerCfg(name);
    if (canvases[name]) {
      canvases[name].style.display = state.visible[name] ? "block" : "none";
      canvases[name].style.opacity = name === state.activeLayer ? 1 : 0.5;
    }
    if (!state.visible[name] || cfg.input !== "polygon") continue;
    renderPolygonLayer(svg, name, cfg);
  }

  renderAssignOverlay(svg);
  renderPointsOverlay(svg);
  if (state.editMode) renderVertexHandles(svg);
  renderDrawing(svg);
  renderTracePreview(svg);
  renderHoverLabel(svg);
}

function renderHoverLabel(svg) {
  const cfg = activeCfg();
  if (!cfg || cfg.input !== "point" || !state.hoverLabel) return;
  const [x, y] = state.hoverLabel.at;
  const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
  text.setAttribute("x", String(x + 10 / state.zoom));
  text.setAttribute("y", String(y - 10 / state.zoom));
  text.setAttribute("class", "hover-label");
  text.setAttribute("font-size", String(14 / state.zoom));
  text.textContent = state.hoverLabel.text;
  svg.appendChild(text);
}

function renderPolygonLayer(svg, name, cfg) {
  const isActive = name === state.activeLayer;

  features(name).forEach((feature, index) => {
    const fill =
      cfg.kind === "identity" ? feature.color || "#808080" : colorForKey(cfg, feature.key);
    const selected = isActive && isSelectedFeature(cfg, feature, index);

    for (const polygon of feature.polygons || []) {
      if (polygon.length < 3) continue;
      const node = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      node.setAttribute("points", polygon.map((p) => p.join(",")).join(" "));
      node.setAttribute("fill", fill);
      node.setAttribute("stroke", selected ? "#fff" : darken(fill));
      node.setAttribute(
        "class",
        "poly-fill" +
          (selected ? " selected" : "") +
          (isActive ? "" : " dim") +
          (state.magnet[name] ? " magnet" : ""),
      );
      if (isAssignTarget(name)) {
        node.classList.add("clickable");
        node.onclick = (e) => {
          e.stopPropagation();
          assignProvince(feature);
        };
      }
      svg.appendChild(node);
    }
  });
}

function isSelectedFeature(cfg, feature, index) {
  return cfg.kind === "identity"
    ? index === state.selected[state.activeLayer]
    : feature.key === state.selected[state.activeLayer];
}

function isAssignTarget(name) {
  const cfg = activeCfg();
  return !!cfg && cfg.input === "assign" && name === state.manifest.province_layer;
}

function renderAssignOverlay(svg) {
  const cfg = activeCfg();
  if (!cfg || cfg.input !== "assign") return;
  const map = assignments(state.activeLayer);

  for (const feature of features(state.manifest.province_layer)) {
    const key = map[String(feature.id)];
    if (!key) continue;
    for (const polygon of feature.polygons || []) {
      if (polygon.length < 3) continue;
      const node = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      node.setAttribute("points", polygon.map((p) => p.join(",")).join(" "));
      node.setAttribute("fill", colorForKey(cfg, key));
      node.setAttribute("class", "poly-fill assign");
      svg.appendChild(node);
    }
  }
}

function assignProvince(feature) {
  const key = state.selected[state.activeLayer];
  if (!key) {
    setStatus("Pick a category on the left first", true);
    return;
  }
  const map = assignments(state.activeLayer);
  // Clicking the same category again clears it, so a mis-assignment is
  // one click to undo rather than needing a separate eraser.
  if (map[String(feature.id)] === key) delete map[String(feature.id)];
  else map[String(feature.id)] = key;
  scheduleAutosave();
  renderFeatureList();
  render();
}

function renderPointsOverlay(svg) {
  for (const name of state.manifest.layer_order) {
    const cfg = layerCfg(name);
    if (cfg.input !== "point" || !state.visible[name]) continue;
    const pts = points(name);
    const isFree = cfg.point_coupling === "free";

    for (const [pid, value] of Object.entries(pts)) {
      const [x, y] = isFree ? [value.x, value.y] : value;
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", x);
      dot.setAttribute("cy", y);
      let cls = "vertex point-marker";
      if (name === state.activeLayer && String(pid) === String(state.selected[name])) {
        cls += " active";
      }
      dot.setAttribute("class", cls);
      if (isFree) {
        const colorField = colorFieldName(cfg);
        const colorValue = colorField ? value[colorField] : null;
        if (colorValue !== null && colorValue !== undefined) {
          dot.setAttribute("fill", colorForKey(cfg, String(colorValue)));
        }
        if (cfg.point_fields?.[colorField]?.type === "tier") {
          // Bigger dot for a bigger city - the same speed that drives
          // growth reads at a glance on the map. The stylesheet's `.vertex
          // { r: 4 }` outranks a plain r="" attribute, so set it inline.
          dot.style.r = `${4 + Number(colorValue || 1)}px`;
        }
        if (name === state.activeLayer) {
          dot.onpointerdown = (e) => beginFreePointDrag(e, name, pid, value);
        }
      }
      svg.appendChild(dot);

      const nameField = isFree ? nameFieldName(cfg) : null;
      const label = nameField ? value[nameField] : null;
      if (label) {
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", String(x));
        text.setAttribute("y", String(y - 10));
        text.setAttribute("class", "point-label");
        text.textContent = label;
        svg.appendChild(text);
      }
    }
  }
}

function beginFreePointDrag(event, name, pointId, value) {
  event.stopPropagation();
  event.preventDefault();
  const start = svgPoint(event);
  let moved = false;

  const onMove = (e) => {
    const at = svgPoint(e);
    if (!moved && Math.hypot(at[0] - start[0], at[1] - start[1]) < 3 / state.zoom) return;
    moved = true;
    value.x = at[0];
    value.y = at[1];
    render();
  };

  const onUp = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    if (!moved) {
      // A click without a drag just selects it for editing.
      state.selected[name] = pointId;
      renderFeatureList();
    }
    scheduleAutosave();
    render();
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

function renderVertexHandles(svg) {
  const feature = currentFeature();
  if (!feature) return;

  (feature.polygons || []).forEach((polygon, polyIndex) => {
    polygon.forEach((point, vertexIndex) => {
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", point[0]);
      dot.setAttribute("cy", point[1]);
      dot.setAttribute("class", "vertex");
      dot.onpointerdown = (e) => beginVertexDrag(e, feature, polygon, vertexIndex, polyIndex);
      svg.appendChild(dot);

      // A midpoint handle inserts a vertex on that edge - the usual way to
      // add detail to a border that's already roughly right.
      const next = polygon[(vertexIndex + 1) % polygon.length];
      const mid = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      mid.setAttribute("cx", String((point[0] + next[0]) / 2));
      mid.setAttribute("cy", String((point[1] + next[1]) / 2));
      mid.setAttribute("class", "vertex mid");
      mid.onpointerdown = (e) => {
        e.stopPropagation();
        e.preventDefault();
        polygon.splice(vertexIndex + 1, 0, [(point[0] + next[0]) / 2, (point[1] + next[1]) / 2]);
        state.snapIndex = null;
        scheduleAutosave();
        render();
      };
      svg.appendChild(mid);
    });
  });
}

function beginVertexDrag(event, feature, polygon, vertexIndex, polyIndex) {
  event.stopPropagation();
  event.preventDefault();
  const start = svgPoint(event);
  let moved = false;

  const onMove = (e) => {
    const at = svgPoint(e);
    if (!moved && Math.hypot(at[0] - start[0], at[1] - start[1]) < 3 / state.zoom) return;
    moved = true;
    polygon[vertexIndex] = snapPoint(at[0], at[1]);
    render();
  };

  const onUp = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    if (!moved) {
      // A click without a drag deletes the vertex - and a ring under three
      // points isn't a shape any more, so it goes with it.
      polygon.splice(vertexIndex, 1);
      if (polygon.length < 3) feature.polygons.splice(polyIndex, 1);
    }
    state.snapIndex = null;
    scheduleAutosave();
    render();
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

function renderDrawing(svg) {
  if (!state.drawing || !state.drawing.length) return;
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", state.drawing.map((p) => p.join(",")).join(" "));
  line.setAttribute("class", "poly-fill pending");
  svg.appendChild(line);

  for (const point of state.drawing) {
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", point[0]);
    dot.setAttribute("cy", point[1]);
    dot.setAttribute("class", "vertex");
    svg.appendChild(dot);
  }
}

function renderTracePreview(svg) {
  if (state.traceAnchor && state.snapIndex) {
    const polyline = state.snapIndex.polylines[state.traceAnchor.lineIndex];
    if (polyline) {
      const rail = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      rail.setAttribute("points", polyline.points.map((p) => p.join(",")).join(" "));
      rail.setAttribute("class", "trace-rail");
      svg.appendChild(rail);
    }
  }
  if (state.tracePreview && state.tracePreview.length > 1) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("points", state.tracePreview.map((p) => p.join(",")).join(" "));
    line.setAttribute("class", "trace-preview");
    svg.appendChild(line);
  }
}

// ---------------------------------------------------------------------------
// pointer input
// ---------------------------------------------------------------------------

function svgPoint(event) {
  const rect = el.overlay.getBoundingClientRect();
  const [width, height] = state.manifest.size;
  return [
    ((event.clientX - rect.left) / rect.width) * width,
    ((event.clientY - rect.top) / rect.height) * height,
  ];
}

function pointInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function provinceAt(x, y) {
  for (const feature of features(state.manifest.province_layer)) {
    for (const polygon of feature.polygons || []) {
      if (polygon.length >= 3 && pointInRing(x, y, polygon)) return feature;
    }
  }
  return null;
}

function onCanvasClick(event) {
  const cfg = activeCfg();
  if (cfg && cfg.input === "point" && cfg.point_coupling === "free") {
    const at = svgPoint(event);
    const id = nextPointId(cfg.name);
    points(cfg.name)[id] = { x: at[0], y: at[1], ...defaultPointPayload(cfg) };
    state.selected[cfg.name] = id;
    scheduleAutosave();
    renderFeatureList();
    render();
    return;
  }
  if (cfg && cfg.input === "point") {
    const pid = state.selected[state.activeLayer];
    if (pid === undefined || pid === null) {
      setStatus("Pick a province on the left first", true);
      return;
    }
    const at = svgPoint(event);
    const under = provinceAt(at[0], at[1]);
    const target = features(state.manifest.province_layer).find(
      (f) => String(f.id) === String(pid),
    );
    if (under && target && under.id !== target.id) {
      setStatus(`That spot is inside "${under.name}", not "${target.name}" - placed anyway`, true);
    } else {
      setStatus(`Placed in "${target ? target.name : pid}"`);
    }
    points(state.activeLayer)[String(pid)] = at;
    scheduleAutosave();
    renderFeatureList();
    render();
    return;
  }
  if (!cfg || cfg.input !== "polygon") return;
  const at = svgPoint(event);

  if (state.tool === "trace") return onTraceClick(at, event.altKey);
  if (state.editMode) return;

  if (!currentFeature()) {
    setStatus("Select or create something to draw into first", true);
    return;
  }
  if (!state.drawing) state.drawing = [];
  state.drawing.push(snapPoint(at[0], at[1]));
  state.snapIndex = null;
  render();
}

function onTraceClick(at, longWay) {
  buildSnapIndex();
  const hit = Trace.nearestOnPolylines(state.snapIndex.polylines, at[0], at[1]);
  if (!hit || hit.dist > SNAP_SCREEN_PX / state.zoom) {
    setStatus("Move closer to a snapped boundary to trace along it", true);
    return;
  }

  if (!state.traceAnchor) {
    state.traceAnchor = hit;
    state.tracePreview = null;
    setStatus("Locked on - click again to take the stretch (Alt = long way)");
    render();
    return;
  }

  if (hit.lineIndex !== state.traceAnchor.lineIndex) {
    // Re-anchoring beats erroring out: a continent then an island is two
    // traces, and this is what the second one starts with.
    state.traceAnchor = hit;
    state.tracePreview = null;
    setStatus("Both points must sit on the same boundary - re-anchored here", true);
    render();
    return;
  }

  const polyline = state.snapIndex.polylines[state.traceAnchor.lineIndex];
  const points = Trace.traceBetween(polyline, state.traceAnchor, hit, {
    longWay,
    epsilon: TRACE_EPSILON_SCREEN / state.zoom,
    maxVertices: MAX_TRACE_VERTICES,
  });

  if (!state.drawing) state.drawing = [];
  const last = state.drawing[state.drawing.length - 1];
  const skip = last && last[0] === points[0][0] && last[1] === points[0][1] ? 1 : 0;
  state.drawing.push(...points.slice(skip));

  // Re-anchor at the end so consecutive traces chain along a coast without
  // re-clicking the start each time.
  state.traceAnchor = hit;
  state.tracePreview = null;
  setStatus(`Traced ${points.length} points - click again to continue`);
  render();
}

function onCanvasMove(event) {
  const cfg = activeCfg();
  if (cfg && cfg.input === "point") {
    const at = svgPoint(event);
    const under = provinceAt(at[0], at[1]);
    state.hoverLabel = under ? { at, text: under.name } : null;
    render();
    return;
  }
  if (!cfg || cfg.input !== "polygon") return;
  if (state.tool !== "trace" || !state.traceAnchor || !state.snapIndex) return;

  const at = svgPoint(event);
  const polyline = state.snapIndex.polylines[state.traceAnchor.lineIndex];
  const hit = Trace.nearestOnPolyline(polyline, at[0], at[1]);
  state.tracePreview = Trace.traceBetween(polyline, state.traceAnchor, hit, {
    longWay: event.altKey,
    epsilon: TRACE_EPSILON_SCREEN / state.zoom,
    maxVertices: MAX_TRACE_VERTICES,
  });
  render();
}

function finishShape() {
  if (!state.drawing || state.drawing.length < 3) {
    setStatus("A shape needs at least 3 points", true);
    return;
  }
  const feature = currentFeature();
  if (!feature) return;
  if (!feature.polygons) feature.polygons = [];
  feature.polygons.push(state.drawing);
  state.drawing = null;
  state.traceAnchor = null;
  state.tracePreview = null;
  state.snapIndex = null;
  scheduleAutosave();
  renderSidebar();
  render();
}

// ---------------------------------------------------------------------------
// brush painting
// ---------------------------------------------------------------------------

function brushColor() {
  return colorForKey(activeCfg(), state.selected[state.activeLayer]);
}

function paintAt(at, erase) {
  const canvas = canvases[state.activeLayer];
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
  ctx.fillStyle = erase ? "#000" : brushColor();
  ctx.beginPath();
  ctx.arc(at[0], at[1], state.brushSize / 2, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalCompositeOperation = "source-over";
  state.dirtyRasters.add(state.activeLayer);
}

function bucketFill() {
  const canvas = canvases[state.activeLayer];
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = brushColor();
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  state.dirtyRasters.add(state.activeLayer);
  scheduleAutosave();
}

// Wipes the whole layer to nodata (transparent), the same state an
// eraser stroke leaves behind. Unpainted pixels fall back to default_key
// at export (export.py), so this is the true "start this layer over" —
// unlike painting default_key opaquely, it doesn't leave a fill that's
// indistinguishable from actually-painted plains.
function resetLayerToDefault() {
  const cfg = activeCfg();
  if (!cfg || !cfg.default_key) return;
  if (!confirm(`Clear all painted "${cfg.title}" (reverts to ${cfg.default_key})?`)) return;
  const canvas = canvases[state.activeLayer];
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  state.dirtyRasters.add(state.activeLayer);
  scheduleAutosave();
  render();
}

function onBrushDown(event) {
  const cfg = activeCfg();
  if (!cfg || cfg.input !== "brush") return;
  event.preventDefault();
  if (state.tool === "bucket") return bucketFill();
  state.painting = true;
  paintAt(svgPoint(event), state.tool === "eraser");
}

function onBrushMove(event) {
  if (!state.painting) return;
  paintAt(svgPoint(event), state.tool === "eraser");
}

function onBrushUp() {
  if (!state.painting) return;
  state.painting = false;
  scheduleAutosave();
}

// A brush layer's raster is its source of truth, so it goes back to disk
// rather than living in project.json.
async function flushRasters() {
  for (const name of Array.from(state.dirtyRasters)) {
    const canvas = canvases[name];
    if (!canvas) continue;
    const blob = await new Promise((resolve) => {
      // Re-flatten transparency onto the layer's nodata color, since that
      // is what "nothing painted here" means on disk.
      const flat = document.createElement("canvas");
      flat.width = canvas.width;
      flat.height = canvas.height;
      const ctx = flat.getContext("2d");
      ctx.fillStyle = layerCfg(name).nodata_color;
      ctx.fillRect(0, 0, flat.width, flat.height);
      ctx.drawImage(canvas, 0, 0);
      flat.toBlob(resolve, "image/png");
    });
    await fetch(`/api/layer/${name}.png`, { method: "POST", body: blob });
    state.dirtyRasters.delete(name);
  }
}

// ---------------------------------------------------------------------------
// server actions
// ---------------------------------------------------------------------------

let autosaveTimer = null;
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(saveDraft, AUTOSAVE_MS);
}

async function saveDraft() {
  await flushRasters();
  await fetch("/api/project", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.project),
  });
  setStatus("Saved");
}

async function runFillGaps() {
  const layer = state.activeLayer;
  setStatus("Filling gaps...");
  await flushRasters();
  const response = await fetch("/api/fillgaps", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layer, project: state.project }),
  });
  const result = await response.json();
  if (!result.ok) return setStatus(result.error, true);

  state.project.layers[layer].features = result.features;
  state.snapIndex = null;
  scheduleAutosave();
  renderSidebar();
  render();

  const residual = result.residual_px
    ? ` ${result.residual_px}px still unclaimed - wider than the ` +
      `${result.max_gap_px}px fill limit, so draw those in.`
    : "";
  setStatus(`Filled ${result.changed_px}px.${residual}`, !!result.residual_px);
}

async function runCleanShapes() {
  const layer = state.activeLayer;
  setStatus("Resolving overlaps and crossings...");
  await flushRasters();
  const response = await fetch("/api/cleanshapes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layer, project: state.project }),
  });
  const result = await response.json();
  if (!result.ok) return setStatus(result.error, true);

  state.project.layers[layer].features = result.features;
  state.snapIndex = null;
  scheduleAutosave();
  renderSidebar();
  render();
  setStatus(
    result.changed_px
      ? `Redrew ${result.changed_px}px of overlapping or crossed borders. ` +
          "Later-drawn provinces kept the contested land - reorder or " +
          "retrace if that's not who should own it."
      : "No crossings or overlaps to fix.",
  );
}

async function runGrowStart() {
  showConfirm(
    "Reset this layer's provinces and reseed them from the current city points?",
    async () => {
      setStatus("Seeding provinces from cities...");
      await flushRasters();
      const response = await fetch("/api/grow/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: state.project }),
      });
      const result = await response.json();
      if (!result.ok) return setStatus(result.error, true);

      const province = state.manifest.province_layer;
      state.project.layers[province].features = result.features;
      state.project.layers[province].growth = result.growth;
      state.snapIndex = null;
      renderSidebar();
      render();
      setStatus(`Seeded ${result.province_count} province(s) from cities. Step 0.`);
    },
  );
}

async function runGrowStep() {
  setStatus("Growing provinces...");
  await flushRasters();
  const response = await fetch("/api/grow/step", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: state.project }),
  });
  const result = await response.json();
  if (!result.ok) return setStatus(result.error, true);

  const province = state.manifest.province_layer;
  state.project.layers[province].features = result.features;
  state.project.layers[province].growth = result.growth;
  state.snapIndex = null;
  renderSidebar();
  render();
  setStatus(
    result.done
      ? `Step ${result.step}: nothing left to grow - every city is done.`
      : `Step ${result.step}: grew ${result.changed_px}px, ` +
          `${result.growing_cities.length} city(ies) still expanding.`,
  );
}

async function runAutotrace() {
  showConfirm("Replace this layer's shapes with a fresh autotrace?", async () => {
    const response = await fetch(`/api/autotrace/${state.activeLayer}`, {
      method: "POST",
    });
    const result = await response.json();
    if (!result.ok) return setStatus(result.error, true);
    state.project.layers[state.activeLayer].features = result.features;
    state.snapIndex = null;
    scheduleAutosave();
    renderSidebar();
    render();
    setStatus(`Autotraced ${result.features.length} shape(s)`);
  });
}

async function runExport() {
  setStatus("Exporting...");
  await flushRasters();
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: state.project }),
  });
  const result = await response.json();
  if (!result.ok) return setStatus(result.error, true);
  let dropped = "";
  if (result.dropped_points?.length) {
    dropped = ` Dropped ${result.dropped_points.length} city point(s) for provinces that no longer exist.`;
    state.project = await (await fetch("/api/project")).json();
    renderFeatureList();
    render();
  }
  setStatus(
    `Exported ${result.province_count} provinces. Run make promote-map to ship it.${dropped}`,
  );
}

// ---------------------------------------------------------------------------
// camera
// ---------------------------------------------------------------------------

function setZoom(zoom, anchor) {
  const [width, height] = state.manifest.size;
  const previous = state.zoom;
  state.zoom = Math.max(0.2, Math.min(6, zoom));

  // Resize in real pixels rather than with a CSS transform, so the
  // scrollable area actually grows and panning stays useful when zoomed in.
  const displayWidth = width * state.zoom;
  const displayHeight = height * state.zoom;
  for (const node of [el.backdrop, el.overlay, ...Object.values(canvases)]) {
    node.style.width = displayWidth + "px";
    node.style.height = displayHeight + "px";
  }

  if (anchor) {
    const scale = state.zoom / previous;
    el.viewport.scrollLeft = (el.viewport.scrollLeft + anchor.x) * scale - anchor.x;
    el.viewport.scrollTop = (el.viewport.scrollTop + anchor.y) * scale - anchor.y;
  }
}

const held = new Set();
let panZoomLast = 0;
function panZoomTick(now) {
  if (!panZoomLast) panZoomLast = now;
  const dt = Math.min(0.05, (now - panZoomLast) / 1000);
  panZoomLast = now;

  const step = PAN_SPEED * dt;
  if (held.has("a")) el.viewport.scrollLeft -= step;
  if (held.has("d")) el.viewport.scrollLeft += step;
  if (held.has("w")) el.viewport.scrollTop -= step;
  if (held.has("s")) el.viewport.scrollTop += step;
  if (held.has("z")) setZoom(state.zoom * Math.exp(ZOOM_SPEED * dt));
  if (held.has("x")) setZoom(state.zoom * Math.exp(-ZOOM_SPEED * dt));

  requestAnimationFrame(panZoomTick);
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------

function bindEvents() {
  el.overlay.addEventListener("click", onCanvasClick);
  el.overlay.addEventListener("pointermove", onCanvasMove);
  el.overlay.addEventListener("pointerleave", () => {
    if (state.hoverLabel) {
      state.hoverLabel = null;
      render();
    }
  });
  el.overlay.addEventListener("dblclick", (e) => {
    e.preventDefault();
    finishShape();
  });

  el.overlay.addEventListener("pointerdown", onBrushDown);
  window.addEventListener("pointermove", onBrushMove);
  window.addEventListener("pointerup", onBrushUp);

  document.getElementById("saveBtn").onclick = saveDraft;
  document.getElementById("exportBtn").onclick = runExport;

  el.viewport.addEventListener(
    "wheel",
    (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const rect = el.viewport.getBoundingClientRect();
      setZoom(state.zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1), {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    },
    { passive: false },
  );

  window.addEventListener("keydown", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target.tagName === "INPUT" || target.tagName === "BUTTON") return;
    const key = event.key.toLowerCase();

    if (key === "enter") return finishShape();
    if (key === "escape") {
      state.drawing = null;
      state.traceAnchor = null;
      state.tracePreview = null;
      return render();
    }
    if (key === "backspace" || key === "delete") {
      if (state.drawing && state.drawing.length) {
        event.preventDefault();
        state.drawing.pop();
        state.snapIndex = null;
        render();
      }
      return;
    }
    if (key === "t" && activeCfg() && activeCfg().input === "polygon") {
      return toggleTrace();
    }
    if ("wasdzx".includes(key)) held.add(key);
  });

  window.addEventListener("keyup", (event) => held.delete(event.key.toLowerCase()));
  window.addEventListener("blur", () => held.clear());
  requestAnimationFrame(panZoomTick);
}

boot();
