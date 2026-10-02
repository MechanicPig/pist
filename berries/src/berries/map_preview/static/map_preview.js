import { MapCanvas } from './canvas.js';

const base = location.pathname;
const canvas = document.querySelector('#map');
const renderer = new MapCanvas(canvas, base, draw);
const ctx = renderer.ctx;
const roomsNode = document.querySelector('#rooms');
const filter = document.querySelector('#filter');
const objectInfo = document.querySelector('#object-info');
let state;
let scale = 1;
let offsetX = 0;
let offsetY = 0;
let drag;
let activeRoom;

function fit() {
  if (!state) return;
  ({ scale, offsetX, offsetY } = renderer.fit(state.rooms));
  draw();
}

function drawEntity(entity, room) {
  const x = room.x + entity.x;
  const y = room.y + entity.y;
  if (!renderer.drawSprite(entity.sprite, x, y)) {
    ctx.strokeStyle = '#f25b9a';
    ctx.lineWidth = 2 / scale;
    ctx.strokeRect(x - 4, y - 4, 8, 8);
  }
}

function entranceBox(entrance) {
  return renderer.entranceBox(entrance, state.rooms.find(item => item.name === entrance.room));
}

function draw() {
  if (!state) return;
  const dpr = devicePixelRatio;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * offsetX, dpr * offsetY);
  const box = renderer.bounds(state.rooms, 100);
  ctx.fillStyle = '#101010';
  ctx.fillRect(box.minX, box.minY, box.maxX - box.minX, box.maxY - box.minY);
  box.rooms.forEach(room => {
    ctx.fillStyle = room.name === activeRoom ? '#172b35' : '#080808';
    ctx.fillRect(room.x, room.y, room.width, room.height);
    renderer.tileRows(room, room.background, '#303941');
    renderer.tileRows(room, room.solids, '#bdc6cd');
    room.respawns.forEach(item => renderer.respawn(item, room));
    room.entities.forEach(entity => drawEntity(entity, room));
    ctx.strokeStyle = room.name === activeRoom ? '#54c6ff' : '#56616a';
    ctx.lineWidth = (room.name === activeRoom ? 2 : 1) / scale;
    ctx.strokeRect(room.x, room.y, room.width, room.height);
  });
  state.entrances.forEach(entrance => {
    const box = entranceBox(entrance);
    if (!box) return;
    ctx.strokeStyle = entrance.available ? '#ffad42' : '#775b37';
    ctx.lineWidth = 2 / scale;
    ctx.strokeRect(box.x, box.y, box.width || 4 / scale, box.height || 4 / scale);
  });
}

function resize() {
  renderer.resize();
  draw();
}

function renderList() {
  const query = filter.value.toLocaleLowerCase();
  roomsNode.replaceChildren();
  state.rooms.filter(room => room.name.toLocaleLowerCase().includes(query)).forEach(room => {
    const button = document.createElement('button');
    button.className = `room${room.name === activeRoom ? ' active' : ''}`;
    button.textContent = room.name;
    button.addEventListener('dblclick', () => reveal(room));
    roomsNode.append(button);
  });
}

function reveal(room) {
  activeRoom = room.name;
  scale = Math.min(canvas.clientWidth / Math.max(room.width * 1.5, 1), canvas.clientHeight / Math.max(room.height * 1.5, 1));
  offsetX = canvas.clientWidth / 2 - (room.x + room.width / 2) * scale;
  offsetY = canvas.clientHeight / 2 - (room.y + room.height / 2) * scale;
  renderList();
  draw();
}

function mapPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left - offsetX) / scale, y: (event.clientY - rect.top - offsetY) / scale };
}

function showObject(item) {
  document.querySelector('#object-id').textContent = item.entityId ?? item.targetSid ?? item.kind ?? '';
  document.querySelector('#object-source').textContent = item.source ?? '';
  document.querySelector('#object-attrs').textContent = JSON.stringify(item.attrs ?? {}, null, 2);
  const open = document.querySelector('#object-open');
  open.hidden = !item.targetSid || !item.available;
  open.onclick = () => openMap(item.targetSid);
  objectInfo.hidden = false;
}

async function openMap(targetSid) {
  const response = await fetch(`${base}/open`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ targetSid }),
  });
  await accept(response);
}

async function navigate(action) {
  await accept(await fetch(`${base}/${action}`, { method: 'POST' }));
}

async function accept(response) {
  const data = await response.json();
  if (!response.ok) return status(data.error ?? '操作失败。');
  state = data;
  document.querySelector('#title').textContent = state.title;
  document.querySelector('#back').disabled = !state.canBack;
  document.querySelector('#forward').disabled = !state.canForward;
  document.querySelector('#home').disabled = !state.canHome;
  activeRoom = undefined;
  renderList();
  fit();
}

function status(message) {
  const node = document.querySelector('#status');
  node.textContent = message;
  node.hidden = false;
  setTimeout(() => { node.hidden = true; }, 2500);
}

canvas.addEventListener('mousedown', event => {
  if (event.button === 1 || event.button === 2) {
    drag = { x: event.clientX, y: event.clientY, offsetX, offsetY };
    canvas.classList.add('panning');
    event.preventDefault();
  }
});
addEventListener('mouseup', () => { drag = undefined; canvas.classList.remove('panning'); });
canvas.addEventListener('mousemove', event => {
  if (!drag) return;
  offsetX = drag.offsetX + event.clientX - drag.x;
  offsetY = drag.offsetY + event.clientY - drag.y;
  draw();
});
canvas.addEventListener('wheel', event => {
  const point = mapPoint(event);
  scale = Math.max(.02, Math.min(8, scale * Math.exp(-event.deltaY * .001)));
  const rect = canvas.getBoundingClientRect();
  offsetX = event.clientX - rect.left - point.x * scale;
  offsetY = event.clientY - rect.top - point.y * scale;
  draw();
  event.preventDefault();
}, { passive: false });
canvas.addEventListener('contextmenu', event => event.preventDefault());
canvas.addEventListener('click', event => {
  const point = mapPoint(event);
  const entrance = state.entrances.find(item => {
    const box = entranceBox(item);
    return box && point.x >= box.x && point.x <= box.x + Math.max(box.width, 4 / scale)
      && point.y >= box.y && point.y <= box.y + Math.max(box.height, 4 / scale);
  });
  if (entrance) return showObject(entrance);
  for (const room of state.rooms) {
    const entity = room.entities.find(item => Math.hypot(room.x + item.x - point.x, room.y + item.y - point.y) <= 8 / scale);
    if (entity) return showObject(entity);
  }
});

filter.addEventListener('input', renderList);
document.querySelector('#fit').addEventListener('click', fit);
document.querySelector('#back').addEventListener('click', () => navigate('back'));
document.querySelector('#forward').addEventListener('click', () => navigate('forward'));
document.querySelector('#home').addEventListener('click', () => navigate('home'));
document.querySelector('#object-close').addEventListener('click', () => { objectInfo.hidden = true; });
addEventListener('resize', resize);
addEventListener('pagehide', () => fetch(`${base}/close`, { method: 'POST', keepalive: true }));

resize();
fetch(`${base}/state`).then(accept).catch(error => status(error.message));
