// --- Upload handling ---
const uploadArea = document.getElementById('upload-area');
const fileInput = document.getElementById('file-input');

uploadArea.addEventListener('click', () => fileInput.click());

uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('drag-over');
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('drag-over');
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('drag-over');
    if (e.dataTransfer.files.length) {
        uploadFiles(e.dataTransfer.files);
    }
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length) {
        uploadFiles(fileInput.files);
    }
});

async function uploadFiles(files) {
    const formData = new FormData();
    for (const file of files) {
        formData.append('files', file);
    }

    uploadArea.innerHTML = '<p>Uploading...</p>';

    try {
        const resp = await fetch('/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        window.location.reload();
    } catch (err) {
        uploadArea.innerHTML = `<p style="color: #e94560">Upload failed: ${err.message}</p>`;
        setTimeout(() => {
            uploadArea.innerHTML = '<p>Drop images here or <label for="file-input" class="file-label">browse</label></p>';
        }, 3000);
    }
}


// --- In-memory image data for anchor checks and range display ---
let currentImages = [];

// Load initial data from the DOM
document.querySelectorAll('.timeline-card').forEach(card => {
    const id = parseInt(card.dataset.id);
    const dateInput = card.querySelector('.date-input');
    currentImages.push({
        id,
        anchor_date: dateInput.classList.contains('anchor') ? dateInput.value : null
    });
});


// --- Client-side date interpolation (mirrors server logic) ---

function datePrecision(dateStr) {
    if (dateStr.length === 4) return 'year';
    if (dateStr.length === 7) return 'month';
    return 'day';
}

function parseDateToMs(dateStr) {
    dateStr = dateStr.trim();
    if (dateStr.length === 4) return new Date(parseInt(dateStr), 6, 1).getTime();      // Jul 1
    if (dateStr.length === 7) {
        const [y, m] = dateStr.split('-').map(Number);
        return new Date(y, m - 1, 15).getTime();  // 15th
    }
    const [y, m, d] = dateStr.split('-').map(Number);
    return new Date(y, m - 1, d).getTime();
}

function formatInterpolatedDate(ms, precision) {
    const dt = new Date(ms);
    if (precision === 'year') {
        return String(dt.getFullYear());
    } else if (precision === 'month') {
        return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`;
    }
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

// Compute the interpolated date for a position in a given ordered list of {id, anchor_date}
function computeDateAtPosition(imageList, posIdx) {
    // Find prev and next anchors (skip the item at posIdx itself)
    let prevDate = null, prevPos = null;
    let nextDate = null, nextPos = null;
    for (let i = posIdx - 1; i >= 0; i--) {
        if (imageList[i].anchor_date) {
            prevDate = imageList[i].anchor_date;
            prevPos = i;
            break;
        }
    }
    for (let i = posIdx + 1; i < imageList.length; i++) {
        if (imageList[i].anchor_date) {
            nextDate = imageList[i].anchor_date;
            nextPos = i;
            break;
        }
    }

    if (prevDate && nextDate) {
        const d1 = parseDateToMs(prevDate);
        const d2 = parseDateToMs(nextDate);
        const totalSlots = nextPos - prevPos;
        const position = posIdx - prevPos;
        const computed = d1 + (d2 - d1) * position / totalSlots;
        const p1 = datePrecision(prevDate);
        const p2 = datePrecision(nextDate);
        const precOrder = { year: 0, month: 1, day: 2 };
        const precision = precOrder[p1] <= precOrder[p2] ? p1 : p2;
        return formatInterpolatedDate(computed, precision);
    } else if (prevDate) {
        return prevDate;
    } else if (nextDate) {
        return nextDate;
    }
    return null;
}


// --- Bounding anchors ---
function findBoundingAnchors(images, idx) {
    let prevId = null, nextId = null;
    let prevDate = null, nextDate = null;
    for (let i = idx - 1; i >= 0; i--) {
        if (images[i].anchor_date) {
            prevId = images[i].id;
            prevDate = images[i].anchor_date;
            break;
        }
    }
    for (let i = idx + 1; i < images.length; i++) {
        if (images[i].anchor_date) {
            nextId = images[i].id;
            nextDate = images[i].anchor_date;
            break;
        }
    }
    return { prevId, prevDate, nextId, nextDate };
}


// --- Hover highlighting ---
function setupHoverHighlights() {
    const cards = timeline.querySelectorAll('.timeline-card');
    cards.forEach((card, idx) => {
        const img = currentImages[idx];
        if (img && img.anchor_date) return;

        card.addEventListener('mouseenter', () => {
            const { prevId, nextId } = findBoundingAnchors(currentImages, idx);
            if (prevId != null) {
                const el = timeline.querySelector(`[data-id="${prevId}"]`);
                if (el) el.classList.add('anchor-highlight');
            }
            if (nextId != null) {
                const el = timeline.querySelector(`[data-id="${nextId}"]`);
                if (el) el.classList.add('anchor-highlight');
            }
        });

        card.addEventListener('mouseleave', () => {
            timeline.querySelectorAll('.anchor-highlight').forEach(el => {
                el.classList.remove('anchor-highlight');
            });
        });
    });
}


// --- Drag date preview ---
let dragPreview = null;
let dragMouseX = 0, dragMouseY = 0;

function createDragPreview() {
    if (dragPreview) return;
    dragPreview = document.createElement('div');
    dragPreview.className = 'drag-date-preview';
    document.body.appendChild(dragPreview);
}

function removeDragPreview() {
    if (dragPreview) {
        dragPreview.remove();
        dragPreview = null;
    }
}

function updateDragPreview(dateStr) {
    if (!dragPreview) return;
    if (dateStr) {
        dragPreview.textContent = dateStr;
        dragPreview.style.display = 'block';
        dragPreview.style.left = dragMouseX + 'px';
        dragPreview.style.top = dragMouseY + 'px';
    } else {
        dragPreview.style.display = 'none';
    }
}

function onDragMouseMove(e) {
    dragMouseX = e.clientX;
    dragMouseY = e.clientY;
    if (dragPreview && dragPreview.style.display !== 'none') {
        dragPreview.style.left = dragMouseX + 'px';
        dragPreview.style.top = dragMouseY + 'px';
    }
}


// --- Drag-and-drop reordering ---
const timeline = document.getElementById('timeline');

setupHoverHighlights();

let draggedItemId = null;

async function handleDragEnd(evt) {
    removeDragPreview();
    document.removeEventListener('mousemove', onDragMouseMove);
    timeline.querySelectorAll('.anchor-highlight').forEach(el => el.classList.remove('anchor-highlight'));
    draggedItemId = null;

    const cards = timeline.querySelectorAll('.timeline-card');
    const order = Array.from(cards).map(c => parseInt(c.dataset.id));

    const draggedId = parseInt(evt.item.dataset.id);
    const draggedImg = currentImages.find(img => img.id === draggedId);

    if (draggedImg && draggedImg.anchor_date) {
        const ok = confirm(
            `This image has an anchor date of ${draggedImg.anchor_date}.\n\n` +
            `Clear the anchor date and let it be recomputed from its new position?`
        );
        if (!ok) {
            refreshTimeline();
            return;
        }
        await fetch(`/anchor/${draggedId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: null }),
        });
    }

    await fetch('/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order }),
    });

    refreshTimeline();
}

function handleDragStart(evt) {
    draggedItemId = parseInt(evt.item.dataset.id);
    createDragPreview();
    document.addEventListener('mousemove', onDragMouseMove);
}

function handleSortChange(evt) {
    // Called whenever the ghost moves to a new position
    // Build the current visual order (excluding the dragged item which is the ghost)
    const cards = timeline.querySelectorAll('.timeline-card');
    const order = Array.from(cards).map(c => ({
        id: parseInt(c.dataset.id),
        anchor_date: c.classList.contains('sortable-ghost')
            ? null  // the dragged item loses its anchor during preview
            : (currentImages.find(img => img.id === parseInt(c.dataset.id)) || {}).anchor_date || null
    }));

    // Find the ghost position (where the item would land)
    const ghostIdx = Array.from(cards).findIndex(c => c.classList.contains('sortable-ghost'));
    if (ghostIdx === -1) return;

    // Compute the date at the ghost position
    const dateStr = computeDateAtPosition(order, ghostIdx);
    updateDragPreview(dateStr);

    // Highlight the bounding anchors
    timeline.querySelectorAll('.anchor-highlight').forEach(el => el.classList.remove('anchor-highlight'));
    const { prevId, nextId } = findBoundingAnchors(order, ghostIdx);
    if (prevId != null) {
        const el = timeline.querySelector(`[data-id="${prevId}"]`);
        if (el) el.classList.add('anchor-highlight');
    }
    if (nextId != null) {
        const el = timeline.querySelector(`[data-id="${nextId}"]`);
        if (el) el.classList.add('anchor-highlight');
    }
}

function initSortable() {
    if (!timeline) return;
    Sortable.create(timeline, {
        animation: 200,
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        dragClass: 'sortable-drag',
        onStart: handleDragStart,
        onChange: handleSortChange,
        onEnd: handleDragEnd,
    });
}

// --- Anchor dates ---
function isValidDate(str) {
    if (!str) return true;
    return /^\d{4}$/.test(str) || /^\d{4}-\d{2}$/.test(str) || /^\d{4}-\d{2}-\d{2}$/.test(str);
}

async function setAnchor(imageId, dateValue, deferResort = false) {
    dateValue = dateValue.trim();
    if (!isValidDate(dateValue)) {
        alert('Enter a date as YYYY, YYYY-MM, or YYYY-MM-DD');
        refreshTimeline();
        return;
    }
    // Always defer resort so the card stays in place for stamping
    await fetch(`/anchor/${imageId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: dateValue || null, defer_resort: true }),
    });
    refreshTimeline();
    startResortCountdown();
}


// --- Delete ---
async function deleteImage(imageId) {
    await fetch(`/image/${imageId}`, { method: 'DELETE' });
    refreshTimeline();
}


// --- Date stamp mode ---
let stampDate = null;

function buildStampCursor(date) {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    const font = 'bold 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.font = font;
    const metrics = ctx.measureText(date);
    const w = Math.ceil(metrics.width) + 12;
    const h = 22;
    canvas.width = w;
    canvas.height = h;
    ctx.font = font;
    ctx.fillStyle = 'rgba(233, 69, 96, 0.9)';
    ctx.beginPath();
    ctx.roundRect(0, 0, w, h, 4);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.textBaseline = 'middle';
    ctx.fillText(date, 6, h / 2);
    return `url(${canvas.toDataURL()}) ${Math.floor(w/2)} ${Math.floor(h/2)}, pointer`;
}

function enterStampMode(date) {
    stampDate = date;
    document.body.classList.add('stamp-mode');
    document.documentElement.style.setProperty('--stamp-cursor', buildStampCursor(date));
    let banner = document.getElementById('stamp-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'stamp-banner';
        document.body.appendChild(banner);
    }
    banner.innerHTML = `Stamping: <strong>${date}</strong> — click images to apply, <span class="stamp-exit">click here or press Esc to exit</span>`;
    banner.style.display = 'flex';
    banner.onclick = exitStampMode;
}

// --- Deferred resort with countdown ---
let resortTimer = null;
let resortCountdown = 0;
let countdownInterval = null;
const RESORT_DELAY = 15;

function getResortPill() {
    let pill = document.getElementById('resort-pill');
    if (!pill) {
        pill = document.createElement('div');
        pill.id = 'resort-pill';
        document.body.appendChild(pill);
    }
    return pill;
}

function updateResortPill() {
    const pill = getResortPill();
    if (resortCountdown > 0) {
        pill.textContent = `sorting in ${resortCountdown}s — click to sort now`;
        pill.style.display = 'block';
        pill.onclick = fireResortNow;
    } else {
        pill.style.display = 'none';
    }
}

async function fireResortNow() {
    cancelResortCountdown();
    updateResortPill();
    await fetch('/resort', { method: 'POST' });
    await animatedRefresh();
}

function startResortCountdown() {
    clearTimeout(resortTimer);
    clearInterval(countdownInterval);
    resortCountdown = RESORT_DELAY;
    updateResortPill();
    countdownInterval = setInterval(() => {
        resortCountdown--;
        updateResortPill();
        if (resortCountdown <= 0) clearInterval(countdownInterval);
    }, 1000);
    resortTimer = setTimeout(async () => {
        clearInterval(countdownInterval);
        resortCountdown = 0;
        updateResortPill();
        await fetch('/resort', { method: 'POST' });
        await animatedRefresh();
    }, RESORT_DELAY * 1000);
}

function cancelResortCountdown() {
    clearTimeout(resortTimer);
    clearInterval(countdownInterval);
    resortTimer = null;
    resortCountdown = 0;
}

function exitStampMode() {
    stampDate = null;
    document.body.classList.remove('stamp-mode');
    document.documentElement.style.removeProperty('--stamp-cursor');
    const banner = document.getElementById('stamp-banner');
    if (banner) banner.style.display = 'none';
    // Resort countdown keeps running independently via the pill
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && stampDate) exitStampMode();
});

function handleStampClick(e, imageId) {
    if (!stampDate) return;
    e.preventDefault();
    e.stopPropagation();
    setAnchor(imageId, stampDate, true);  // defer resort
    startResortCountdown();
}

function activateStamp(e, date) {
    e.stopPropagation();
    if (stampDate === date) {
        exitStampMode();
    } else {
        enterStampMode(date);
    }
}


// --- FLIP animation for resort ---
async function animatedRefresh() {
    // FIRST: record current positions
    const oldCards = timeline.querySelectorAll('.timeline-card');
    const firstPositions = {};
    oldCards.forEach(card => {
        const id = card.dataset.id;
        const rect = card.getBoundingClientRect();
        firstPositions[id] = { x: rect.left, y: rect.top };
    });

    // Fetch new data and rebuild DOM
    const resp = await fetch('/timeline-data');
    const images = await resp.json();

    currentImages = images.map(img => ({
        id: img.id,
        anchor_date: img.anchor_date
    }));

    const empty = document.querySelector('.empty-state');
    if (empty) empty.remove();

    renderGroupedTimeline(images);

    // LAST: record new positions and INVERT + PLAY
    const newCards = timeline.querySelectorAll('.timeline-card');
    newCards.forEach(card => {
        const id = card.dataset.id;
        const newRect = card.getBoundingClientRect();
        const oldPos = firstPositions[id];
        if (!oldPos) return;

        const dx = oldPos.x - newRect.left;
        const dy = oldPos.y - newRect.top;
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;

        card.style.transform = `translate(${dx}px, ${dy}px)`;
        card.style.transition = 'none';
        // Force reflow
        card.offsetHeight;
        card.style.transition = 'transform 0.5s ease';
        card.style.transform = '';
        card.addEventListener('transitionend', () => {
            card.style.transition = '';
        }, { once: true });
    });

    initSortableFlat();
    setupHoverHighlights();
}


// --- Render a card's HTML ---
function renderCard(img, rangeHtml) {
    const dateVal = img.anchor_date || img.computed_date || '';
    const stampBtn = dateVal
        ? `<button class="stamp-btn" onclick="activateStamp(event, '${dateVal}')" title="Stamp this date onto other images">&#8633;</button>`
        : '';
    return `
        <div class="timeline-card" data-id="${img.id}" onclick="handleStampClick(event, ${img.id})">
            <div class="card-image">
                <img src="/thumbnails/${img.filename}" alt="${img.original_name}" loading="lazy">
                <button class="delete-btn" onclick="deleteImage(${img.id})" title="Remove">&times;</button>
            </div>
            <div class="card-info">
                <div class="card-name" title="${img.original_name}">${img.original_name}</div>
                <div class="card-date">
                    <input type="text"
                           value="${img.anchor_date || ''}"
                           placeholder="YYYY or YYYY-MM-DD"
                           onchange="setAnchor(${img.id}, this.value)"
                           onkeydown="if(event.key==='Enter'){this.blur()}"
                           class="date-input ${img.anchor_date ? 'anchor' : ''}"
                           title="${img.anchor_date ? 'Anchor date (click to change)' : 'Set date: 2024, 2024-06, or 2024-06-15'}">
                    ${stampBtn}
                    ${img.computed_date && !img.anchor_date ? `<span class="computed-date">${img.computed_date}</span>` : ''}
                    ${rangeHtml}
                </div>
            </div>
        </div>`;
}


// --- Year grouping ---
let collapsedYears = new Set();

function getYear(img) {
    const d = img.anchor_date || img.computed_date;
    if (!d) return null;
    return d.substring(0, 4);
}

function groupByYear(images) {
    const groups = [];
    const groupMap = {};
    images.forEach((img, idx) => {
        const year = getYear(img);
        const key = year || '__undated__';
        if (!groupMap[key]) {
            groupMap[key] = { year: year, images: [], indices: [] };
            groups.push(groupMap[key]);
        }
        groupMap[key].images.push(img);
        groupMap[key].indices.push(idx);
    });
    return groups;
}

function buildRangeHtml(images, idx) {
    if (images[idx].computed_date && !images[idx].anchor_date) {
        const { prevDate, nextDate } = findBoundingAnchors(images, idx);
        if (prevDate && nextDate) {
            return `<span class="computed-range">${prevDate} &larr;&middot;&middot;&middot;&rarr; ${nextDate}</span>`;
        } else if (prevDate) {
            return `<span class="computed-range">after ${prevDate}</span>`;
        } else if (nextDate) {
            return `<span class="computed-range">before ${nextDate}</span>`;
        }
    }
    return '';
}

function renderGroupedTimeline(images) {
    const groups = groupByYear(images);
    const sidebar = document.getElementById('year-sidebar');

    // Build sidebar with year labels
    sidebar.innerHTML = groups.map(g => {
        const year = g.year || 'undated';
        const isCollapsed = collapsedYears.has(year);
        return `<div class="year-label ${isCollapsed ? 'collapsed' : ''}" data-year="${year}" onclick="scrollToYear('${year}')">${year}<span class="year-count"> ${g.images.length}</span></div>`;
    }).join('');

    // Build flat card list — each card tagged with its year for collapse
    let html = '';
    images.forEach((img, idx) => {
        const year = getYear(img) || 'undated';
        const isCollapsed = collapsedYears.has(year);
        const card = renderCard(img, buildRangeHtml(images, idx));
        if (isCollapsed) {
            html += card.replace('class="timeline-card', `class="timeline-card year-collapsed" data-year="${year}`);
        } else {
            html += card.replace('data-id=', `data-year="${year}" data-id=`);
        }
    });
    timeline.innerHTML = html;
}

function toggleYear(year) {
    if (collapsedYears.has(year)) {
        collapsedYears.delete(year);
    } else {
        collapsedYears.add(year);
    }
    // Toggle cards with matching data-year
    timeline.querySelectorAll(`.timeline-card[data-year="${year}"]`).forEach(card => {
        card.classList.toggle('year-collapsed');
    });
    const label = document.querySelector(`.year-label[data-year="${year}"]`);
    if (label) label.classList.toggle('collapsed');
}

function scrollToYear(year) {
    if (collapsedYears.has(year)) {
        toggleYear(year);
    }
    const firstCard = timeline.querySelector(`.timeline-card[data-year="${year}"]`);
    if (firstCard) {
        firstCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

function initSortableFlat() {
    if (!timeline) return;
    Sortable.create(timeline, {
        animation: 200,
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        dragClass: 'sortable-drag',
        onStart(evt) {
            handleDragStart(evt);
            document.body.classList.add('dragging');
        },
        onChange: handleSortChange,
        onEnd(evt) {
            document.body.classList.remove('dragging');
            handleDragEnd(evt);
        },
    });
}


// --- Refresh timeline without full page reload ---
async function refreshTimeline() {
    const resp = await fetch('/timeline-data');
    const images = await resp.json();

    // Update in-memory data
    currentImages = images.map(img => ({
        id: img.id,
        anchor_date: img.anchor_date
    }));

    if (images.length === 0) {
        timeline.innerHTML = '';
        document.getElementById('year-sidebar').innerHTML = '';
        const empty = document.querySelector('.empty-state');
        if (!empty) {
            const main = document.querySelector('main');
            main.innerHTML += '<div class="empty-state"><p>Upload some images to get started.</p></div>';
        }
        return;
    }

    const empty = document.querySelector('.empty-state');
    if (empty) empty.remove();

    renderGroupedTimeline(images);
    initSortableFlat();
    setupHoverHighlights();
}

// Refresh on load so JS-rendered cards (with stamp buttons) replace server-rendered ones
if (document.querySelectorAll('.timeline-card').length > 0) {
    refreshTimeline();
}
