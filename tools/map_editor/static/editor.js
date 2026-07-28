'use strict';

const PALETTE = [
  '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4',
  '#46f0f0', '#bfef45', '#fabed4', '#469990', '#dcbeff', '#9a6324',
  '#808000', '#ffd8b1', '#c65102', '#aaffc3', '#800000', '#000075',
];

const state = {
  imageSize: [0, 0],
  regions: [],          // [{name, color, polygons: [[[x,y],...], ...]}]
  activeRegionIndex: -1,
  drawing: null,        // [[x,y], ...] while placing a new shape's points
  editMode: false,
  selectedVertex: null, // {ri, pi, vi}
  dragging: null,       // {ri, pi, vi, moved}
};

const els = {
  bgImage: document.getElementById('bgImage'),
  overlay: document.getElementById('overlay'),
  stage: document.getElementById('stage'),
  viewport: document.getElementById('viewport'),
  regionList: document.getElementById('regionList'),
  newRegionBtn: document.getElementById('newRegionBtn'),
  newShapeBtn: document.getElementById('newShapeBtn'),
  editVerticesBtn: document.getElementById('editVerticesBtn'),
  saveBtn: document.getElementById('saveBtn'),
  exportBtn: document.getElementById('exportBtn'),
  reloadBtn: document.getElementById('reloadBtn'),
  status: document.getElementById('status'),
};

let zoom = 1;
let autosaveTimer = null;

// In-page replacements for window.prompt/confirm: native dialogs are
// blocking and don't reliably surface in every embedding, which read as
// "the button does nothing". These render inside the page instead.
function showModal({ title, withInput, defaultValue, confirmLabel }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    const box = document.createElement('div');
    box.className = 'modal-box';

    const h = document.createElement('div');
    h.className = 'modal-title';
    h.textContent = title;
    box.appendChild(h);

    let input = null;
    if (withInput) {
      input = document.createElement('input');
      input.type = 'text';
      input.value = defaultValue || '';
      input.className = 'modal-input';
      box.appendChild(input);
    }

    const row = document.createElement('div');
    row.className = 'modal-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    const okBtn = document.createElement('button');
    okBtn.textContent = confirmLabel || 'OK';
    okBtn.className = 'primary';
    row.append(cancelBtn, okBtn);
    box.appendChild(row);

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    const finish = (value) => { document.body.removeChild(overlay); resolve(value); };
    cancelBtn.onclick = () => finish(withInput ? null : false);
    okBtn.onclick = () => finish(withInput ? (input ? input.value : true) : true);
    overlay.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); okBtn.click(); }
      if (e.key === 'Escape') { e.preventDefault(); cancelBtn.click(); }
    });

    if (input) { input.focus(); input.select(); }
    else okBtn.focus();
  });
}

function showPrompt(title, defaultValue) {
  return showModal({ title, withInput: true, defaultValue });
}

function showConfirm(title, confirmLabel) {
  return showModal({ title, withInput: false, confirmLabel });
}

function setStatus(msg, isError) {
  els.status.textContent = msg;
  els.status.className = isError ? 'error' : '';
}

function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(saveDraft, 1500);
}

async function saveDraft() {
  await fetch('/api/project', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_size: state.imageSize, regions: state.regions }),
  });
  setStatus('Draft saved.');
}

async function exportProject() {
  const res = await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_size: state.imageSize, regions: state.regions }),
  });
  const data = await res.json();
  if (data.ok) {
    setStatus(`Exported ${data.region_count} regions -> ${data.region_map}. Run "make promote-map" to ship it.`);
  } else {
    setStatus('Export failed: ' + data.error, true);
  }
}

function applyProject(proj) {
  state.regions = proj.regions || [];
  state.activeRegionIndex = -1;
  state.drawing = null;
  state.editMode = false;
  syncButtons();
  renderRegionList();
  render();
}

async function reloadFromGame() {
  const ok = await showConfirm('Discard current draft and re-trace from the map currently shipped in campaign/map_data?', 'Reload');
  if (!ok) return;
  const res = await fetch('/api/reload-from-game');
  const data = await res.json();
  if (data.error) {
    setStatus(data.error, true);
    return;
  }
  applyProject(data);
  setStatus(`Loaded ${data.regions.length} regions from the live game map.`);
  scheduleAutosave();
}

function nextColor() {
  return PALETTE[state.regions.length % PALETTE.length];
}

async function newRegion() {
  const name = await showPrompt('Region name:');
  if (!name || !name.trim()) return;
  state.regions.push({ name: name.trim(), color: nextColor(), polygons: [] });
  state.activeRegionIndex = state.regions.length - 1;
  state.drawing = [];
  state.editMode = false;
  syncButtons();
  renderRegionList();
  render();
}

function newShape() {
  if (state.activeRegionIndex < 0) return;
  state.drawing = [];
  state.editMode = false;
  syncButtons();
  render();
}

function toggleEditVertices() {
  if (state.activeRegionIndex < 0) return;
  state.editMode = !state.editMode;
  state.drawing = null;
  syncButtons();
  render();
}

function syncButtons() {
  const hasActive = state.activeRegionIndex >= 0;
  els.newShapeBtn.disabled = !hasActive;
  els.editVerticesBtn.disabled = !hasActive;
  els.editVerticesBtn.textContent = 'Edit Vertices: ' + (state.editMode ? 'On' : 'Off');
  els.editVerticesBtn.classList.toggle('active', state.editMode);
}

function setActiveRegion(idx) {
  state.activeRegionIndex = idx;
  state.drawing = null;
  state.editMode = false;
  syncButtons();
  renderRegionList();
  render();
}

async function deleteRegion(idx) {
  const ok = await showConfirm(`Delete region "${state.regions[idx].name}"?`, 'Delete');
  if (!ok) return;
  state.regions.splice(idx, 1);
  if (state.activeRegionIndex === idx) state.activeRegionIndex = -1;
  else if (state.activeRegionIndex > idx) state.activeRegionIndex--;
  syncButtons();
  renderRegionList();
  render();
  scheduleAutosave();
}

function renderRegionList() {
  els.regionList.innerHTML = '';
  state.regions.forEach((region, idx) => {
    const row = document.createElement('div');
    row.className = 'region-row' + (idx === state.activeRegionIndex ? ' active' : '');

    const colorInput = document.createElement('input');
    colorInput.type = 'color';
    colorInput.value = region.color;
    colorInput.oninput = (e) => { region.color = e.target.value; render(); scheduleAutosave(); };
    colorInput.onclick = (e) => e.stopPropagation();

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = region.name;
    nameInput.oninput = (e) => { region.name = e.target.value; scheduleAutosave(); };
    nameInput.onclick = (e) => e.stopPropagation();

    const count = document.createElement('span');
    count.className = 'shape-count';
    count.textContent = `${region.polygons.length} shape${region.polygons.length === 1 ? '' : 's'}`;

    const del = document.createElement('button');
    del.className = 'del';
    del.textContent = '✕';
    del.onclick = (e) => { e.stopPropagation(); deleteRegion(idx); };

    row.append(colorInput, nameInput, count, del);
    row.onclick = () => setActiveRegion(idx);
    els.regionList.appendChild(row);
  });
}

function darken(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.max(0, (n >> 16) - 40), g = Math.max(0, ((n >> 8) & 255) - 40), b = Math.max(0, (n & 255) - 40);
  return `rgb(${r},${g},${b})`;
}

function svgNS(tag) { return document.createElementNS('http://www.w3.org/2000/svg', tag); }

function pointsAttr(pts) { return pts.map(p => p[0] + ',' + p[1]).join(' '); }

function render() {
  const svg = els.overlay;
  svg.innerHTML = '';

  state.regions.forEach((region, ri) => {
    const isActive = ri === state.activeRegionIndex;
    region.polygons.forEach((poly) => {
      const el = svgNS('polygon');
      el.setAttribute('points', pointsAttr(poly));
      el.setAttribute('class', 'poly-fill' + (isActive ? ' selected' : ''));
      el.setAttribute('fill', region.color);
      el.setAttribute('stroke', isActive ? '#ffffff' : darken(region.color));
      svg.appendChild(el);
    });
  });

  // in-progress shape
  if (state.drawing) {
    const el = svgNS('polyline');
    el.setAttribute('points', pointsAttr(state.drawing));
    el.setAttribute('class', 'poly-fill pending');
    svg.appendChild(el);
    state.drawing.forEach((p) => {
      const c = svgNS('circle');
      c.setAttribute('cx', p[0]); c.setAttribute('cy', p[1]);
      c.setAttribute('class', 'vertex');
      svg.appendChild(c);
    });
  }

  // editable vertices for the active region
  if (state.editMode && state.activeRegionIndex >= 0) {
    const region = state.regions[state.activeRegionIndex];
    region.polygons.forEach((poly, pi) => {
      poly.forEach((p, vi) => {
        const c = svgNS('circle');
        c.setAttribute('cx', p[0]); c.setAttribute('cy', p[1]);
        c.setAttribute('class', 'vertex');
        c.dataset.ri = state.activeRegionIndex; c.dataset.pi = pi; c.dataset.vi = vi;
        c.addEventListener('pointerdown', onVertexPointerDown);
        svg.appendChild(c);

        // midpoint marker to insert a new vertex on this edge
        const q = poly[(vi + 1) % poly.length];
        const mid = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
        const m = svgNS('circle');
        m.setAttribute('cx', mid[0]); m.setAttribute('cy', mid[1]);
        m.setAttribute('class', 'vertex mid');
        m.addEventListener('click', (e) => {
          e.stopPropagation();
          poly.splice(vi + 1, 0, mid);
          render();
          scheduleAutosave();
        });
        svg.appendChild(m);
      });
    });
  }
}

// Snapping: while placing/dragging a point, pull it onto any nearby
// existing vertex or edge (from any region, including the drawing in
// progress) so adjacent territories share an exact border instead of
// leaving slivers/gaps. Radius is defined in screen px so it feels
// consistent at any zoom level.
const SNAP_SCREEN_PX = 14;

function closestPointOnSegment(px, py, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return [a[0], a[1]];
  let t = ((px - a[0]) * dx + (py - a[1]) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return [a[0] + t * dx, a[1] + t * dy];
}

function collectSnapCandidates({ excludeRi, excludePi, excludeVi } = {}) {
  const points = [];
  const edges = [];
  state.regions.forEach((region, ri) => {
    region.polygons.forEach((poly, pi) => {
      poly.forEach((p, vi) => {
        if (ri === excludeRi && pi === excludePi && vi === excludeVi) return;
        points.push(p);
      });
      const n = poly.length;
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        if (ri === excludeRi && pi === excludePi && (i === excludeVi || j === excludeVi)) continue;
        edges.push([poly[i], poly[j]]);
      }
    });
  });
  if (state.drawing) {
    state.drawing.forEach((p) => points.push(p));
    for (let i = 0; i < state.drawing.length - 1; i++) edges.push([state.drawing[i], state.drawing[i + 1]]);
  }
  return { points, edges };
}

function snapPoint(x, y, exclude) {
  const radius = SNAP_SCREEN_PX / zoom;
  const { points, edges } = collectSnapCandidates(exclude || {});
  let best = null, bestDist = radius;
  for (const p of points) {
    const d = Math.hypot(p[0] - x, p[1] - y);
    if (d < bestDist) { bestDist = d; best = [p[0], p[1]]; }
  }
  for (const [a, b] of edges) {
    const proj = closestPointOnSegment(x, y, a, b);
    const d = Math.hypot(proj[0] - x, proj[1] - y);
    if (d < bestDist) { bestDist = d; best = proj; }
  }
  return best;
}

function svgPoint(evt) {
  const pt = els.overlay.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  const ctm = els.overlay.getScreenCTM().inverse();
  const p = pt.matrixTransform(ctm);
  return [Math.round(p.x), Math.round(p.y)];
}

function onVertexPointerDown(e) {
  e.stopPropagation();
  const ri = +e.target.dataset.ri, pi = +e.target.dataset.pi, vi = +e.target.dataset.vi;
  state.dragging = { ri, pi, vi, moved: false, startX: e.clientX, startY: e.clientY };
  e.target.setPointerCapture(e.pointerId);
}

els.overlay.addEventListener('pointermove', (e) => {
  if (!state.dragging) return;
  const d = state.dragging;
  if (Math.abs(e.clientX - d.startX) + Math.abs(e.clientY - d.startY) > 3) d.moved = true;
  if (!d.moved) return;
  let [x, y] = svgPoint(e);
  const snapped = snapPoint(x, y, { excludeRi: d.ri, excludePi: d.pi, excludeVi: d.vi });
  if (snapped) [x, y] = snapped;
  state.regions[d.ri].polygons[d.pi][d.vi] = [x, y];
  render();
});

els.overlay.addEventListener('pointerup', (e) => {
  if (!state.dragging) return;
  const d = state.dragging;
  if (!d.moved) {
    // treat as click-to-delete
    const poly = state.regions[d.ri].polygons[d.pi];
    if (poly.length <= 3) {
      state.regions[d.ri].polygons.splice(d.pi, 1);
    } else {
      poly.splice(d.vi, 1);
    }
    render();
  }
  state.dragging = null;
  scheduleAutosave();
});

els.overlay.addEventListener('click', (e) => {
  if (state.dragging) return;
  if (state.drawing === null) return;
  let [x, y] = svgPoint(e);
  const snapped = snapPoint(x, y);
  if (snapped) [x, y] = snapped;
  state.drawing.push([x, y]);
  render();
});

els.overlay.addEventListener('dblclick', (e) => {
  e.preventDefault();
  finishShape();
});

function finishShape() {
  if (!state.drawing || state.drawing.length < 3) {
    if (state.drawing) setStatus('Need at least 3 points to close a shape.', true);
    return;
  }
  state.regions[state.activeRegionIndex].polygons.push(state.drawing);
  state.drawing = null;
  renderRegionList();
  render();
  scheduleAutosave();
}

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'Enter') { finishShape(); }
  else if (e.key === 'Escape') { state.drawing = null; render(); }
  else if (e.key === 'Backspace' || e.key === 'Delete') {
    e.preventDefault();
    if (state.drawing && state.drawing.length) { state.drawing.pop(); render(); }
  }
});

// Zooms so the given viewport-local point (or the viewport center, if
// omitted) stays under the cursor/screen-center as the scale changes.
// Resizes the image + svg overlay directly (rather than a CSS transform)
// so #viewport's scrollable area actually grows with zoom.
function setZoom(newZoom, anchorX, anchorY) {
  newZoom = Math.min(6, Math.max(0.2, newZoom));
  const rect = els.viewport.getBoundingClientRect();
  if (anchorX === undefined) { anchorX = rect.width / 2; anchorY = rect.height / 2; }
  const contentX = (els.viewport.scrollLeft + anchorX) / zoom;
  const contentY = (els.viewport.scrollTop + anchorY) / zoom;
  zoom = newZoom;
  const w = state.imageSize[0] * zoom, h = state.imageSize[1] * zoom;
  els.bgImage.style.width = w + 'px';
  els.bgImage.style.height = h + 'px';
  els.overlay.style.width = w + 'px';
  els.overlay.style.height = h + 'px';
  els.viewport.scrollLeft = contentX * zoom - anchorX;
  els.viewport.scrollTop = contentY * zoom - anchorY;
}

els.viewport.addEventListener('wheel', (e) => {
  if (!e.ctrlKey && !e.metaKey) return; // allow normal two-finger pan scroll otherwise
  e.preventDefault();
  const rect = els.viewport.getBoundingClientRect();
  setZoom(zoom * Math.exp(-e.deltaY * 0.001), e.clientX - rect.left, e.clientY - rect.top);
}, { passive: false });

// WASD pan / Z,X zoom, held-key continuous movement (like a game camera).
const PAN_KEYS = new Set(['w', 'a', 's', 'd', 'z', 'x']);
const heldKeys = new Set();
const PAN_SPEED = 1000; // viewport px/sec
const ZOOM_SPEED = 1.2; // e-folds/sec

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  const key = e.key.toLowerCase();
  if (PAN_KEYS.has(key)) heldKeys.add(key);
});
document.addEventListener('keyup', (e) => {
  heldKeys.delete(e.key.toLowerCase());
});
window.addEventListener('blur', () => heldKeys.clear());

let lastFrameTime = null;
function panZoomTick(now) {
  if (lastFrameTime === null) lastFrameTime = now;
  const dt = Math.min(0.1, (now - lastFrameTime) / 1000);
  lastFrameTime = now;

  if (heldKeys.size) {
    const dist = PAN_SPEED * dt;
    if (heldKeys.has('w')) els.viewport.scrollTop -= dist;
    if (heldKeys.has('s')) els.viewport.scrollTop += dist;
    if (heldKeys.has('a')) els.viewport.scrollLeft -= dist;
    if (heldKeys.has('d')) els.viewport.scrollLeft += dist;
    if (heldKeys.has('z')) setZoom(zoom * Math.exp(-ZOOM_SPEED * dt));
    if (heldKeys.has('x')) setZoom(zoom * Math.exp(ZOOM_SPEED * dt));
  }
  requestAnimationFrame(panZoomTick);
}
requestAnimationFrame(panZoomTick);

els.newRegionBtn.onclick = newRegion;
els.newShapeBtn.onclick = newShape;
els.editVerticesBtn.onclick = toggleEditVertices;
els.saveBtn.onclick = saveDraft;
els.exportBtn.onclick = exportProject;
els.reloadBtn.onclick = reloadFromGame;

async function init() {
  const proj = await (await fetch('/api/project')).json();

  els.bgImage.src = '/api/image?' + Date.now();
  await new Promise((resolve) => { els.bgImage.onload = resolve; });

  const w = els.bgImage.naturalWidth, h = els.bgImage.naturalHeight;
  state.imageSize = [w, h];
  els.overlay.setAttribute('width', w);
  els.overlay.setAttribute('height', h);
  els.overlay.setAttribute('viewBox', `0 0 ${w} ${h}`);
  els.bgImage.style.width = w + 'px';
  els.bgImage.style.height = h + 'px';

  applyProject(proj);
  setStatus(state.regions.length ? `Loaded ${state.regions.length} regions.` : 'New project. Click "+ New Region" to start tracing.');
}

init();
