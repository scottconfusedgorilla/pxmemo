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


// --- Bounding anchors ---
// For a given image index in currentImages, find the IDs of its prev/next anchors
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
        if (img && img.anchor_date) return; // anchors don't need this

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

// --- Drag-and-drop reordering ---
const timeline = document.getElementById('timeline');

setupHoverHighlights();

async function handleDragEnd(evt) {
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

function initSortable() {
    if (!timeline) return;
    Sortable.create(timeline, {
        animation: 200,
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        onEnd: handleDragEnd,
    });
}

initSortable();


// --- Anchor dates ---
function isValidDate(str) {
    if (!str) return true;
    return /^\d{4}$/.test(str) || /^\d{4}-\d{2}$/.test(str) || /^\d{4}-\d{2}-\d{2}$/.test(str);
}

async function setAnchor(imageId, dateValue) {
    dateValue = dateValue.trim();
    if (!isValidDate(dateValue)) {
        alert('Enter a date as YYYY, YYYY-MM, or YYYY-MM-DD');
        refreshTimeline();
        return;
    }
    await fetch(`/anchor/${imageId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: dateValue || null }),
    });
    refreshTimeline();
}


// --- Delete ---
async function deleteImage(imageId) {
    await fetch(`/image/${imageId}`, { method: 'DELETE' });
    refreshTimeline();
}


// --- Render a card's HTML ---
function renderCard(img, rangeHtml) {
    return `
        <div class="timeline-card" data-id="${img.id}">
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
                    ${img.computed_date && !img.anchor_date ? `<span class="computed-date">${img.computed_date}</span>` : ''}
                    ${rangeHtml}
                </div>
            </div>
        </div>`;
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
        const empty = document.querySelector('.empty-state');
        if (!empty) {
            const main = document.querySelector('main');
            main.innerHTML += '<div class="empty-state"><p>Upload some images to get started.</p></div>';
        }
        return;
    }

    const empty = document.querySelector('.empty-state');
    if (empty) empty.remove();

    // Build HTML with range info for interpolated cards
    timeline.innerHTML = images.map((img, idx) => {
        let rangeHtml = '';
        if (img.computed_date && !img.anchor_date) {
            const { prevDate, nextDate } = findBoundingAnchors(images, idx);
            if (prevDate && nextDate) {
                rangeHtml = `<span class="computed-range">${prevDate} &larr;&middot;&middot;&middot;&rarr; ${nextDate}</span>`;
            } else if (prevDate) {
                rangeHtml = `<span class="computed-range">after ${prevDate}</span>`;
            } else if (nextDate) {
                rangeHtml = `<span class="computed-range">before ${nextDate}</span>`;
            }
        }
        return renderCard(img, rangeHtml);
    }).join('');

    initSortable();
    setupHoverHighlights();
}
