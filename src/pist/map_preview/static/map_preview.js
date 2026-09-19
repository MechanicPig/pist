const base = location.pathname;
const canvas = document.querySelector('#map');
const ctx = canvas.getContext('2d');
const filter = document.querySelector('#filter');
const roomsNode = document.querySelector('#rooms');
const count = document.querySelector('#count');
const collectibleSummary = document.querySelector('#collectible-summary');
const hint = document.querySelector('#hint');
const entityInfo = document.querySelector('#entity-info');
const entityInfoId = document.querySelector('#entity-info-id');
const entityInfoSource = document.querySelector('#entity-info-source');
const entityInfoAttrs = document.querySelector('#entity-info-attrs');
const save = document.querySelector('#save');
const showFirstClearRoute = document.querySelector('#show-first-clear-route');
const showFirstClearTime = document.querySelector('#show-first-clear-time');
const selected = new Set();
const roomCounts = new Map();
const roomByName = new Map();
let state;
let mode = 'review';
let highlightedRoom;
let scale = 1;
let offsetX = 0;
let offsetY = 0;
let dragging;
let selectionBox;
let finished = false;
let lastMiddleDown;
let lastRightClick;
let selectedRoomToReveal;
let canvasSelectedRoom;
let flashingRoom;
let flashTimer;
let excludedEntities = new Set();
let openingMap = false;

const MAP_MARGIN = 100;
const DOUBLE_CLICK_DELAY = 350;
const DRAG_THRESHOLD = 4;
const sprites = new Map();
const spriteFiles = {
  strawberry: 'strawberry.png',
  moonberry: 'moonberry.png',
  goldenberry: 'goldenberry.png',
  cassette: 'cassette.png',
  heart: 'heart.png',
};

for (const [name, file] of Object.entries(spriteFiles)) {
  loadSprite(name, file);
}

function loadSprite(name, file = name) {
  if (sprites.has(name)) return sprites.get(name);
  const image = new Image();
  image.src = `${base}/game-assets/${file}`;
  image.addEventListener('load', draw);
  sprites.set(name, image);
  return image;
}

function bounds(rooms) {
  const visible = rooms.filter(room => Number.isFinite(room.x) && Number.isFinite(room.y) && Number.isFinite(room.width) && Number.isFinite(room.height));
  return {
    rooms: visible,
    minX: Math.min(...visible.map(room => room.x)),
    minY: Math.min(...visible.map(room => room.y)),
    maxX: Math.max(...visible.map(room => room.x + room.width)),
    maxY: Math.max(...visible.map(room => room.y + room.height)),
  };
}

function previewBounds(rooms) {
  const box = bounds(rooms);
  return {
    ...box,
    minX: box.minX - MAP_MARGIN,
    minY: box.minY - MAP_MARGIN,
    maxX: box.maxX + MAP_MARGIN,
    maxY: box.maxY + MAP_MARGIN,
  };
}

function resize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = devicePixelRatio;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  draw();
}

function setupView() {
  const box = previewBounds(state.rooms);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  scale = Math.min(width / Math.max(box.maxX - box.minX, 1), height / Math.max(box.maxY - box.minY, 1));
  offsetX = (width - (box.maxX - box.minX) * scale) / 2 - box.minX * scale;
  offsetY = (height - (box.maxY - box.minY) * scale) / 2 - box.minY * scale;
}

function tileRows(room, rows, color) {
  ctx.fillStyle = color;
  for (let y = 0; y < rows.length; y++) {
    const row = rows[y];
    let begin = -1;
    for (let x = 0; x <= row.length; x++) {
      if (x < row.length && row[x] !== '0') {
        if (begin < 0) begin = x;
      } else if (begin >= 0) {
        ctx.fillRect(room.x + begin * 8, room.y + y * 8, (x - begin) * 8, 8);
        begin = -1;
      }
    }
  }
}

function drawEntity(entity, room) {
  const x = room.x + entity.x;
  const y = room.y + entity.y;
  const excluded = entity.key && excludedEntities.has(entity.key);
  if (excluded) ctx.globalAlpha = .25;
  if (entity.kind === 'audit') {
    ctx.strokeStyle = '#f25b9a';
    ctx.lineWidth = 2 / scale;
    ctx.strokeRect(x - 4 / scale, y - 4 / scale, 8 / scale, 8 / scale);
    if (excluded) ctx.globalAlpha = 1;
    return;
  }
  const legacyName = entity.kind === 'strawberry' || entity.kind === 'moonberry' || entity.kind === 'cassette'
    ? entity.kind : entity.kind.includes('goldenberry') ? 'goldenberry' : entity.kind.includes('heart') ? 'heart' : undefined;
  if (!drawSprite(entity.sprite ?? legacyName, x, y)) {
    ctx.strokeStyle = '#f25b9a';
    ctx.lineWidth = 2 / scale;
    ctx.strokeRect(x - 4, y - 4, 8, 8);
  }
  if (excluded) ctx.globalAlpha = 1;
}

function respawn(item, room) {
  const x = room.x + item.x;
  const y = room.y + item.y;
  ctx.fillStyle = '#c43d3d';
  ctx.fillRect(Math.floor(x / 8) * 8, Math.floor((y - 8) / 8) * 8, 8, 8);
}

function drawSprite(name, x, y) {
  if (!name) return false;
  const image = loadSprite(name);
  if (!image?.complete || !image.naturalWidth) return false;
  ctx.drawImage(image, Math.round(x - image.naturalWidth / 2), Math.round(y - image.naturalHeight));
  return true;
}

function entranceBox(entrance, room) {
  if (!room || !Number.isFinite(entrance.x) || !Number.isFinite(entrance.y)) return;
  const width = Number.isFinite(entrance.width) ? entrance.width : 0;
  const height = Number.isFinite(entrance.height) ? entrance.height : 0;
  return { x: room.x + entrance.x, y: room.y + entrance.y, width, height };
}

function drawEntrance(entrance, room) {
  const box = entranceBox(entrance, room);
  if (!box) return;
  const x = box.x + box.width / 2;
  const color = entrance.available ? '#ffad42' : '#775b37';
  if (box.width || box.height) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5 / scale;
    ctx.strokeRect(box.x, box.y, box.width, box.height);
  }
  if (mode !== 'navigate') return;
  ctx.font = `${12 / scale}px sans-serif`;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  const label = entrance.targetTitle;
  const metrics = ctx.measureText(label);
  const labelX = x - metrics.width / 2;
  const labelY = box.y - 9 / scale;
  drawOutlinedText(label, labelX, labelY, color);
}

function drawOutlinedText(text, x, y, color) {
  ctx.lineWidth = 3 / scale;
  ctx.strokeStyle = '#000';
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

function draw() {
  if (!state) return;
  const dpr = devicePixelRatio;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * offsetX, dpr * offsetY);
  const box = previewBounds(state.rooms);
  ctx.fillStyle = '#101010';
  ctx.fillRect(box.minX, box.minY, box.maxX - box.minX, box.maxY - box.minY);
  for (const room of box.rooms) {
    ctx.fillStyle = '#080808';
    ctx.fillRect(room.x, room.y, room.width, room.height);
    tileRows(room, room.background, '#303941');
    tileRows(room, room.solids, '#bdc6cd');
    if (!room.respawns.length) {
      ctx.fillStyle = 'rgba(0, 0, 0, .48)';
      ctx.fillRect(room.x, room.y, room.width, room.height);
    }
  }
  const order = new Map();
  if (showFirstClearRoute.checked) {
    state.firstClearRooms.forEach((name, index) => { if (!order.has(name)) order.set(name, index + 1); });
    ctx.strokeStyle = '#58d8ef';
    ctx.lineWidth = 2 / scale;
    ctx.setLineDash([5 / scale, 4 / scale]);
    ctx.beginPath();
    let started = false;
    for (const name of state.firstClearRooms) {
      const room = roomByName.get(name);
      if (!room) continue;
      const x = room.x + room.width / 2;
      const y = room.y + room.height / 2;
      if (started) ctx.lineTo(x, y);
      else { ctx.moveTo(x, y); started = true; }
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }
  for (const room of box.rooms) {
    const highlighted = highlightedRoom === room.name;
    ctx.strokeStyle = highlighted ? '#f06464' : selected.has(room.name) ? '#75ee47' : '#65717d';
    ctx.lineWidth = (highlighted || selected.has(room.name) ? 3 : 1) / scale;
    ctx.strokeRect(room.x, room.y, room.width, room.height);
    for (const entity of room.entities) drawEntity(entity, room);
    for (const item of room.respawns) respawn(item, room);
    for (const entrance of state.entrances) if (entrance.room === room.name) drawEntrance(entrance, room);
    const index = order.get(room.name);
    if (index) {
      ctx.font = `${15 / scale}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      drawOutlinedText(String(index), room.x + room.width / 2, room.y + room.height / 2, '#58d8ef');
    }
    if (showFirstClearTime.checked && Number.isFinite(room.firstClearTime)) {
      ctx.font = `${12 / scale}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const y = room.y + room.height / 2 + (index ? 14 : 0) / scale;
      drawOutlinedText(formatTime(room.firstClearTime), room.x + room.width / 2, y, '#d2d9de');
    }
  }
  if (selectionBox) {
    ctx.strokeStyle = '#54c6ff';
    ctx.lineWidth = 1.5 / scale;
    ctx.setLineDash([4 / scale, 3 / scale]);
    ctx.strokeRect(selectionBox.x, selectionBox.y, selectionBox.width, selectionBox.height);
    ctx.setLineDash([]);
  }
}

function renderList() {
  const query = filter.value.trim().toLocaleLowerCase();
  roomsNode.replaceChildren();
  for (const room of state.rooms) {
    if (query && !room.name.toLocaleLowerCase().includes(query)) continue;
    const row = document.createElement('div');
    row.className = 'room';
    row.classList.toggle('canvas-selected', room.name === canvasSelectedRoom);
    row.classList.toggle('flash', room.name === flashingRoom);
    const text = document.createElement('span');
    text.className = 'room-name';
    text.textContent = room.name || '(未命名房间)';
    if (mode === 'review') {
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = selected.has(room.name);
      input.addEventListener('click', event => event.stopPropagation());
      input.addEventListener('dblclick', event => event.stopPropagation());
      input.addEventListener('change', () => {
        if (input.checked) selected.add(room.name);
        else selected.delete(room.name);
        canvasSelectedRoom = undefined;
        update();
      });
      row.append(input);
    }
    const content = document.createElement('div');
    content.className = 'room-content';
    content.append(text);
    if (mode === 'review') {
      const adjustment = document.createElement('div');
      adjustment.className = 'room-adjustment';
      const respawns = document.createElement('span');
      respawns.className = 'respawn-count';
      respawns.textContent = `检测到 ${room.respawnCount} 个存档点`;
      const label = document.createElement('label');
      label.textContent = '实际房间';
      const decrement = document.createElement('button');
      decrement.type = 'button';
      decrement.className = 'room-count-button';
      decrement.textContent = '−';
      const input = document.createElement('input');
      input.type = 'text';
      input.inputMode = 'numeric';
      input.pattern = '[0-9]*';
      input.value = String(roomCounts.get(room.name) ?? 1);
      input.className = 'room-count-input';
      const increment = document.createElement('button');
      increment.type = 'button';
      increment.className = 'room-count-button';
      increment.textContent = '+';
      const stop = event => event.stopPropagation();
      for (const control of [decrement, input, increment]) {
        control.addEventListener('click', stop);
        control.addEventListener('dblclick', stop);
      }
      decrement.addEventListener('click', () => setRoomCount(room, (roomCounts.get(room.name) ?? 1) - 1));
      increment.addEventListener('click', () => setRoomCount(room, (roomCounts.get(room.name) ?? 1) + 1));
      input.addEventListener('change', () => setRoomCount(room, Number(input.value)));
      label.append(decrement, input, increment);
      adjustment.append(respawns, label);
      content.append(adjustment);
    }
    const firstClear = firstClearData(room);
    if (firstClear) {
      const data = document.createElement('span');
      data.className = 'room-data';
      const time = document.createElement('span');
      time.textContent = firstClear.time;
      const death = document.createElement('span');
      death.textContent = firstClear.death;
      data.append(time, death);
      content.append(data);
    }
    row.append(content);
    row.addEventListener('click', () => {
      highlightedRoom = highlightedRoom === room.name ? undefined : room.name;
      if (canvasSelectedRoom === room.name) {
        canvasSelectedRoom = undefined;
      } else {
        canvasSelectedRoom = room.name;
        selectedRoomToReveal = room.name;
      }
      update();
    });
    row.addEventListener('dblclick', () => {
      highlightedRoom = room.name;
      canvasSelectedRoom = room.name;
      selectedRoomToReveal = room.name;
      update();
      centerRoom(room);
    });
    roomsNode.append(row);
    if (room.name === selectedRoomToReveal) {
      roomsNode.scrollTop = Math.max(0, row.offsetTop - (roomsNode.clientHeight - row.offsetHeight) / 2);
    }
  }
  selectedRoomToReveal = undefined;
}

function firstClearData(room) {
  if (!Number.isFinite(room.firstClearTime) && !Number.isFinite(room.firstClearDeath)) return;
  return {
    time: Number.isFinite(room.firstClearTime) ? formatTime(room.firstClearTime) : '',
    death: Number.isFinite(room.firstClearDeath) ? String(room.firstClearDeath) : '',
  };
}

function formatTime(milliseconds) {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds % 3600 / 60);
  const seconds = totalSeconds % 60;
  return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function update() {
  const reviewing = mode === 'review';
  const correctingCollectibles = mode === 'collectibles';
  const readOnly = state.readOnly;
  count.hidden = !reviewing;
  save.hidden = !reviewing && !correctingCollectibles;
  count.textContent = `已选择 ${selected.size} / ${state.rooms.length}（实际 ${selectedRoomCount()}）`;
  const summary = collectibleSummaryText();
  collectibleSummary.textContent = summary;
  collectibleSummary.hidden = !summary;
  hint.textContent = readOnly
    ? '只读预览；滚轮缩放；中键或右键拖动画布；中键双击复位；点击房间定位。'
    : reviewing
    ? '左键切换房间；列表可调整每个房间的实际数量（默认 1，检测到的存档点仅供参考）；从空白处拖框批量选择（Ctrl 减去，Shift+Ctrl 对称差）；中键或右键拖动画布；中键双击复位。'
    : correctingCollectibles
    ? '左键定位房间；右键切换已命中收集品的可获得状态；中键或右键拖动画布；中键双击复位。'
    : '左键定位房间；右键查看实体属性，右键双击橙色入口跳转；中键或右键拖动画布；中键双击复位。';
  document.querySelector('#back').disabled = !state.canBack;
  document.querySelector('#forward').disabled = !state.canForward;
  document.querySelector('#home').disabled = !state.canHome;
  for (const id of ['back', 'forward', 'home']) {
    document.querySelector(`#${id}`).hidden = readOnly || mode !== 'navigate';
  }
  renderList();
  draw();
}

function selectedRoomCount() {
  return [...selected].reduce((total, name) => total + (roomCounts.get(name) ?? 1), 0);
}

function setRoomCount(room, count) {
  const normalized = Number.isInteger(count) ? Math.max(1, count) : 1;
  roomCounts.set(room.name, normalized);
  update();
}

function collectibleSummaryText() {
  const groups = new Map();
  for (const room of state.rooms) {
    for (const entity of room.entities) {
      if (!entity.summary || !entity.key) continue;
      const key = entity.summary.kind;
      let group = groups.get(key);
      if (!group) {
        group = { ...entity.summary, total: 0, active: 0, values: new Set() };
        groups.set(key, group);
      }
      group.total += 1;
      if (!excludedEntities.has(entity.key)) {
        group.active += 1;
        if (entity.summary.stat === 'select' && entity.summary.value) {
          group.values.add(entity.summary.value);
        }
      }
    }
  }
  return [...groups.values()].map(group => {
    if (group.stat === 'count') return `${group.label} ${group.active}/${group.total}`;
    if (group.stat === 'exist') return `${group.label} ${group.active ? '有' : '无'}（${group.active}/${group.total}）`;
    const values = [...group.values];
    if (values.length === 0) return `${group.label} 无`;
    if (values.length === 1) return `${group.label} ${values[0]}（${group.active}/${group.total}）`;
    return `${group.label} 冲突（${values.join(' / ')}）`;
  }).join(' · ');
}

function revealRoom(name) {
  selectedRoomToReveal = name;
  flashingRoom = name;
  if (flashTimer !== undefined) clearTimeout(flashTimer);
  update();
  flashTimer = setTimeout(() => {
    flashingRoom = undefined;
    renderList();
  }, 750);
}

function centerRoom(room) {
  offsetX = canvas.clientWidth / 2 - (room.x + room.width / 2) * scale;
  offsetY = canvas.clientHeight / 2 - (room.y + room.height / 2) * scale;
  draw();
}

function roomAt(point) {
  return [...bounds(state.rooms).rooms].reverse().find(room =>
    point.x >= room.x && point.x < room.x + room.width && point.y >= room.y && point.y < room.y + room.height
  );
}

function nearestEntrance(point) {
  const maximumDistance = 16 / scale;
  return state.entrances
    .filter(entrance => entrance.available)
    .map(entrance => ({ entrance, box: entranceBox(entrance, roomByName.get(entrance.room)) }))
    .filter(item => item.box)
    .map(item => ({
      ...item,
      distance: Math.hypot(
        point.x - (item.box.x + item.box.width / 2),
        point.y - (item.box.y + item.box.height / 2),
      ),
      contains: point.x >= item.box.x && point.x <= item.box.x + item.box.width
        && point.y >= item.box.y && point.y <= item.box.y + item.box.height,
    }))
    .filter(item => item.contains || item.distance <= maximumDistance)
    .sort((left, right) => left.distance - right.distance)[0]?.entrance;
}

function nearestCollectibleEntity(point) {
  const maximumDistance = 12 / scale;
  return state.rooms
    .flatMap(room => room.entities.map(entity => ({ room, entity })))
    .filter(item => item.entity.key && item.entity.summary)
    .map(item => ({
      ...item,
      distance: Math.hypot(
        point.x - (item.room.x + item.entity.x),
        point.y - (item.room.y + item.entity.y),
      ),
    }))
    .filter(item => item.distance <= maximumDistance)
    .sort((left, right) => left.distance - right.distance)[0]?.entity;
}

function nearestInspectableEntity(point) {
  const maximumDistance = 12 / scale;
  return state.rooms
    .flatMap(room => room.entities.map(entity => ({ room, entity })))
    .filter(item => item.entity.entityId)
    .map(item => ({
      ...item,
      distance: Math.hypot(
        point.x - (item.room.x + item.entity.x),
        point.y - (item.room.y + item.entity.y),
      ),
    }))
    .filter(item => item.distance <= maximumDistance)
    .sort((left, right) => left.distance - right.distance)[0];
}

function showInspectable(item, event) {
  entityInfoId.textContent = item.entity.entityId;
  entityInfoSource.textContent = `${item.entity.kind} · ${item.room.name}`;
  entityInfoAttrs.textContent = JSON.stringify(item.entity.attrs, null, 2);
  entityInfo.style.left = `${Math.min(event.clientX + 12, innerWidth - 24)}px`;
  entityInfo.style.top = `${Math.min(event.clientY + 12, innerHeight - 24)}px`;
  entityInfo.hidden = false;
}

function toggleRoom(room) {
  if (mode === 'review') {
    if (selected.has(room.name)) selected.delete(room.name);
    else selected.add(room.name);
    revealRoom(room.name);
    return;
  }
  if (mode === 'inspect') {
    if (canvasSelectedRoom === room.name) {
      canvasSelectedRoom = undefined;
      highlightedRoom = undefined;
    } else {
      canvasSelectedRoom = room.name;
      highlightedRoom = room.name;
      selectedRoomToReveal = room.name;
    }
    update();
    return;
  }
  if (canvasSelectedRoom === room.name) {
    canvasSelectedRoom = undefined;
  } else {
    canvasSelectedRoom = room.name;
    selectedRoomToReveal = room.name;
  }
  update();
}

function updateSelectionBox(point) {
  const start = dragging.start;
  selectionBox = {
    x: Math.min(start.x, point.x),
    y: Math.min(start.y, point.y),
    width: Math.abs(point.x - start.x),
    height: Math.abs(point.y - start.y),
  };
}

function applySelectionBox(event) {
  if (!selectionBox) return;
  const rooms = bounds(state.rooms).rooms.filter(room =>
    room.x < selectionBox.x + selectionBox.width
    && room.x + room.width > selectionBox.x
    && room.y < selectionBox.y + selectionBox.height
    && room.y + room.height > selectionBox.y,
  );
  for (const room of rooms) {
    if (event.ctrlKey && event.shiftKey) {
      if (selected.has(room.name)) selected.delete(room.name);
      else selected.add(room.name);
    } else if (event.ctrlKey) {
      selected.delete(room.name);
    } else {
      selected.add(room.name);
    }
  }
}

function world(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left - offsetX) / scale, y: (event.clientY - rect.top - offsetY) / scale };
}

canvas.addEventListener('mousedown', event => {
  if (event.button === 1) {
    event.preventDefault();
    if (lastMiddleDown !== undefined && event.timeStamp - lastMiddleDown <= DOUBLE_CLICK_DELAY) {
      lastMiddleDown = undefined;
      setupView();
      draw();
      return;
    }
    lastMiddleDown = event.timeStamp;
    dragging = { button: event.button, x: event.clientX, y: event.clientY, moved: false };
    canvas.classList.add('panning');
    return;
  }
  if (event.button === 2) {
    event.preventDefault();
    dragging = { button: event.button, x: event.clientX, y: event.clientY, moved: false };
    canvas.classList.add('panning');
    return;
  }
  if (event.button !== 0) return;
  const point = world(event);
  dragging = {
    button: event.button,
    x: event.clientX,
    y: event.clientY,
    start: point,
    startsInRoom: roomAt(point) !== undefined,
    moved: false,
  };
});
addEventListener('mouseup', event => {
  if (!dragging || event.button !== dragging.button) return;
  const drag = dragging;
  dragging = undefined;
  canvas.classList.remove('panning');
  if (drag.button === 1) return;
  if (drag.button === 2) {
    if (drag.moved) return;
    const point = world(event);
    const isDoubleClick = lastRightClick !== undefined
      && event.timeStamp - lastRightClick.time <= DOUBLE_CLICK_DELAY
      && Math.hypot(point.x - lastRightClick.point.x, point.y - lastRightClick.point.y) <= 12 / scale;
    lastRightClick = isDoubleClick ? undefined : { time: event.timeStamp, point };
    if (mode === 'navigate' && isDoubleClick) {
      const entrance = nearestEntrance(point);
      if (entrance) openMap(entrance.targetSid);
    } else if (!drag.moved && mode === 'collectibles') {
      const entity = nearestCollectibleEntity(point);
      if (entity?.key) {
        if (excludedEntities.has(entity.key)) excludedEntities.delete(entity.key);
        else excludedEntities.add(entity.key);
        update();
      }
    } else {
      const inspectable = nearestInspectableEntity(point);
      if (inspectable) showInspectable(inspectable, event);
    }
    return;
  }
  if (selectionBox) {
    applySelectionBox(event);
    selectionBox = undefined;
    update();
  } else if (!drag.moved) {
    const hit = roomAt(world(event));
    if (hit) toggleRoom(hit);
  }
});
canvas.addEventListener('mousemove', event => {
  if (!dragging) return;
  const dx = event.clientX - dragging.x;
  const dy = event.clientY - dragging.y;
  if (!dragging.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
  dragging.moved = true;
  if (dragging.button === 1 || dragging.button === 2) {
    offsetX += dx;
    offsetY += dy;
  } else if (mode === 'review' && !dragging.startsInRoom) {
    updateSelectionBox(world(event));
  }
  dragging.x = event.clientX;
  dragging.y = event.clientY;
  if (dragging.button === 1) lastMiddleDown = undefined;
  draw();
});
canvas.addEventListener('wheel', event => {
  event.preventDefault();
  const before = world(event);
  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
  scale = Math.min(8, Math.max(.02, scale * factor));
  const rect = canvas.getBoundingClientRect();
  offsetX = event.clientX - rect.left - before.x * scale;
  offsetY = event.clientY - rect.top - before.y * scale;
  draw();
}, { passive: false });
canvas.addEventListener('auxclick', event => {
  if (event.button === 1) event.preventDefault();
});
canvas.addEventListener('contextmenu', event => event.preventDefault());
document.querySelector('#entity-info-close').addEventListener('click', () => { entityInfo.hidden = true; });

function loadState(next) {
  const initial = state === undefined;
  state = next;
  if (state.readOnly) mode = 'inspect';
  else if (initial) mode = 'review';
  selected.clear();
  roomCounts.clear();
  excludedEntities = new Set(
    state.rooms.flatMap(room => room.entities.filter(entity => entity.excluded).map(entity => entity.key)),
  );
  roomByName.clear();
  highlightedRoom = undefined;
  canvasSelectedRoom = undefined;
  lastRightClick = undefined;
  state.rooms.forEach(room => {
    roomByName.set(room.name, room);
    roomCounts.set(room.name, room.roomCount ?? 1);
  });
  state.selected.forEach(name => selected.add(name));
  document.title = `地图预览：${state.title}`;
  document.querySelector('#title').textContent = state.title;
  document.querySelector('#mode').hidden = state.readOnly;
  document.querySelector('#overlays').hidden = state.readOnly;
  document.querySelector('#cancel').textContent = state.readOnly ? '关闭' : '取消';
  resize();
  setupView();
  update();
}

async function openMap(targetSid) {
  if (openingMap) return;
  openingMap = true;
  try {
    const response = await fetch(`${base}/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targetSid }),
    });
    const data = await response.json();
    if (!response.ok) { showError(data.error); return; }
    loadState(data);
  } finally {
    openingMap = false;
  }
}

async function navigate(action) {
  const response = await fetch(`${base}/${action}`, { method: 'POST' });
  const data = await response.json();
  if (data.closed) { finishPage('已返回，可关闭此页面。'); return; }
  if (!response.ok) { showError(data.error); return; }
  loadState(data);
}

function showError(message) {
  const status = document.querySelector('#status');
  status.textContent = message || '操作失败。';
  status.hidden = false;
}

function showStatus(message) {
  document.querySelector('#status').textContent = message;
  document.querySelector('#status').hidden = false;
}

function finishPage(message) {
  finished = true;
  showStatus(message);
  document.querySelectorAll('button').forEach(button => { button.disabled = true; });
}

async function saveRoute() {
  const response = await fetch(`${base}/save`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rooms: [...selected], roomCounts: Object.fromEntries([...selected].map(name => [name, roomCounts.get(name) ?? 1])), excludedEntities: [...excludedEntities] }) });
  if (!response.ok) { showError((await response.json()).error); return; }
  showStatus('已保存。');
}

async function cancel() {
  const response = await fetch(`${base}/cancel`, { method: 'POST' });
  const data = await response.json();
  if (!response.ok) { showError(data.error); return; }
  loadState(data);
  showStatus('已还原到上次保存的结果。');
}

document.querySelectorAll('input[name="mode"]').forEach(input => {
  input.addEventListener('change', () => {
    if (input.checked) {
      mode = input.value;
      update();
    }
  });
});
showFirstClearRoute.addEventListener('change', draw);
showFirstClearTime.addEventListener('change', draw);
filter.addEventListener('input', renderList);
document.querySelector('#save').addEventListener('click', saveRoute);
document.querySelector('#cancel').addEventListener('click', cancel);
document.querySelector('#back').addEventListener('click', () => navigate('back'));
document.querySelector('#forward').addEventListener('click', () => navigate('forward'));
document.querySelector('#home').addEventListener('click', () => navigate('home'));
addEventListener('mousedown', event => {
  const action = event.button === 3 ? 'back' : event.button === 4 ? 'forward' : undefined;
  if (action) {
    event.preventDefault();
    event.stopPropagation();
    if ((action !== 'back' || state.canBack) && (action !== 'forward' || state.canForward)) {
      navigate(action);
    }
  }
}, { capture: true });
addEventListener('resize', resize);
new ResizeObserver(resize).observe(canvas);
addEventListener('pagehide', () => {
  if (!finished) fetch(`${base}/abandon`, { method: 'POST', keepalive: true });
});
(async () => { loadState(await (await fetch(`${base}/state`)).json()); })();
