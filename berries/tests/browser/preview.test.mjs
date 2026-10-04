import assert from 'node:assert/strict';
import test from 'node:test';
import { CanvasInteraction, MapViewport } from '../../src/berries/map_preview/static/viewport.js';
import { MapGeometry } from '../../src/berries/map_preview/static/geometry.js';
import { PreviewClient } from '../../src/berries/map_preview/static/client.js';
import { ObjectInfoPanel } from '../../src/berries/map_preview/static/object_info.js';

class Canvas extends EventTarget {
  clientWidth = 800;
  clientHeight = 400;
  classes = new Set();
  classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) };
  getBoundingClientRect() { return { left: 10, top: 20 }; }
}

function mouse(target, type, properties = {}) {
  const event = new Event(type, { cancelable: true });
  Object.defineProperties(event, Object.fromEntries(Object.entries({
    button: 0, clientX: 20, clientY: 30, ...properties,
  }).map(([name, value]) => [name, { value }])));
  target.dispatchEvent(event);
  return event;
}

function scene() {
  const canvas = new Canvas();
  const viewport = new MapViewport(canvas);
  globalThis.window = new EventTarget();
  return { canvas, viewport };
}

test('zoom preserves the world coordinate under the pointer, including at scale limits', () => {
  const { viewport } = scene();
  viewport.fit({ minX: -100, minY: 50, maxX: 300, maxY: 250 });
  const event = { clientX: 250, clientY: 120 };
  const before = viewport.world(event);
  for (const factor of [2, 1000, .000001]) {
    viewport.zoom(event, factor);
    const after = viewport.world(event);
    assert.ok(Math.abs(after.x - before.x) < 1e-9);
    assert.ok(Math.abs(after.y - before.y) < 1e-9);
    assert.ok(viewport.scale >= .02 && viewport.scale <= 8);
  }
});

test('centering a room can preserve zoom or fit the room', () => {
  const { viewport } = scene();
  const room = { x: 100, y: -100, width: 200, height: 100 };
  viewport.scale = 3;
  viewport.center(room);
  assert.equal(viewport.scale, 3);
  assert.deepEqual(viewport.world({ clientX: 410, clientY: 220 }), { x: 200, y: -50 });
  viewport.center(room, true);
  assert.equal(viewport.scale, 400 / 150);
});

test('right click inspects while right drag pans without inspecting', () => {
  const { canvas, viewport } = scene();
  const clicks = [];
  const interaction = new CanvasInteraction(viewport, { redraw() {}, click: (_, event) => clicks.push(event.button) });
  mouse(canvas, 'mousedown', { button: 2 });
  mouse(window, 'mouseup', { button: 2 });
  assert.deepEqual(clicks, [2]);
  mouse(canvas, 'mousedown', { button: 2 });
  mouse(canvas, 'mousemove', { clientX: 60, clientY: 70 });
  mouse(window, 'mouseup', { button: 2 });
  assert.deepEqual(clicks, [2]);
  assert.equal(viewport.offsetX, 40);
  assert.equal(viewport.offsetY, 40);
  assert.equal(canvas.classes.size, 0);
  interaction.dispose();
});

test('left drag delegates world-space selection and modifier keys, never pans', () => {
  const { canvas, viewport } = scene();
  const moves = [];
  const ends = [];
  const interaction = new CanvasInteraction(viewport, {
    redraw() {}, dragMove: (start, point) => moves.push({ start, point }),
    dragEnd: event => ends.push(event.ctrlKey), click: () => assert.fail('drag must not click'),
  });
  mouse(canvas, 'mousedown');
  mouse(canvas, 'mousemove', { clientX: 23, clientY: 30 });
  assert.equal(moves.length, 0);
  mouse(canvas, 'mousemove', { clientX: 70, clientY: 80 });
  mouse(window, 'mouseup', { ctrlKey: true });
  assert.deepEqual(moves, [{ start: { x: 10, y: 10 }, point: { x: 60, y: 60 } }]);
  assert.deepEqual(ends, [true]);
  assert.equal(viewport.offsetX, 0);
  interaction.dispose();
});

test('small pointer jitter still clicks and ignored mouse buttons do not start gestures', () => {
  const { canvas, viewport } = scene();
  const clicks = [];
  const interaction = new CanvasInteraction(viewport, { redraw() {}, click: point => clicks.push(point) });
  mouse(canvas, 'mousedown');
  mouse(canvas, 'mousemove', { clientX: 22 });
  mouse(window, 'mouseup', { clientX: 22 });
  assert.deepEqual(clicks, [{ x: 12, y: 10 }]);
  mouse(canvas, 'mousedown', { button: 3 });
  mouse(canvas, 'mousemove', { clientX: 100 });
  mouse(window, 'mouseup', { button: 3 });
  assert.equal(clicks.length, 1);
  assert.equal(viewport.offsetX, 0);
  interaction.dispose();
});

test('middle double click resets, drag cancels double click, blur and disposal cancel gestures', () => {
  const { canvas, viewport } = scene();
  let resets = 0;
  let cancelled = 0;
  const interaction = new CanvasInteraction(viewport, {
    redraw() {}, reset: () => resets++, dragCancel: () => cancelled++,
  });
  mouse(canvas, 'mousedown', { button: 1, timeStamp: 100 });
  mouse(window, 'mouseup', { button: 1 });
  mouse(canvas, 'mousedown', { button: 1, timeStamp: 300 });
  assert.equal(resets, 1);
  mouse(canvas, 'mousedown', { button: 1, timeStamp: 400 });
  mouse(canvas, 'mousemove', { clientX: 60 });
  mouse(window, 'mouseup', { button: 1 });
  mouse(canvas, 'mousedown', { button: 1, timeStamp: 500 });
  assert.equal(resets, 1);
  window.dispatchEvent(new Event('blur'));
  assert.equal(canvas.classes.size, 0);
  interaction.dispose();
  const offset = viewport.offsetX;
  mouse(canvas, 'mousedown', { button: 2 });
  mouse(canvas, 'mousemove', { clientX: 100 });
  assert.equal(viewport.offsetX, offset);
  assert.ok(cancelled >= 2);
});

test('room queries preserve stacking, half-open edges and strict box intersection', () => {
  const geometry = new MapGeometry();
  const first = { name: 'first', x: 0, y: 0, width: 100, height: 100, entities: [] };
  const upper = { ...first, name: 'upper', x: 50 };
  geometry.load([first, upper, { ...first, name: 'invalid', x: NaN }], []);
  assert.equal(geometry.roomAt({ x: 60, y: 20 }), upper);
  assert.equal(geometry.roomAt({ x: 150, y: 20 }), undefined);
  assert.deepEqual(geometry.roomsIntersecting({ x: 100, y: 0, width: 20, height: 20 }), [upper]);
  assert.deepEqual(geometry.roomsIntersecting({ x: -10, y: -10, width: 10, height: 10 }), []);
});

test('entity queries support first-hit and nearest-inspectable policies without sorting', () => {
  const geometry = new MapGeometry();
  const anonymous = { x: 10, y: 10 };
  const far = { x: 14, y: 10, entityId: 'far' };
  const near = { x: 12, y: 10, entityId: 'near' };
  geometry.load([{ name: 'room', x: 100, y: 0, width: 100, height: 100, entities: [{ x: NaN, y: 10 }, anonymous, far, near] }], []);
  const point = { x: 110, y: 10 };
  assert.equal(geometry.entityAt(point, { radius: 8 }).entity, anonymous);
  assert.equal(geometry.entityAt(point, { radius: 8, nearest: true, filter: item => Boolean(item.entityId) }).entity, near);
  assert.equal(geometry.entityAt(point, { radius: 1, filter: item => Boolean(item.entityId) }), undefined);
});

test('entrances handle local offsets, zero-sized hits, invalid rooms and nearest distance', () => {
  const geometry = new MapGeometry();
  const point = { room: 'room', x: 10, y: 10 };
  const wide = { room: 'room', x: 8, y: 8, width: 100, height: 20 };
  geometry.load([{ name: 'room', x: 100, y: 50, width: 200, height: 100, entities: [] }], [point, wide, { room: 'missing', x: 0, y: 0 }]);
  assert.equal(geometry.entranceAt({ x: 113, y: 63 }, { minimumSize: 4 }), point);
  assert.equal(geometry.entranceAt({ x: 112, y: 60 }, { radius: 16, nearest: true }), point);
  assert.equal(geometry.entranceAt({ x: 190, y: 60 }, { radius: 1, nearest: true }), wide);
  assert.equal(geometry.entranceAt({ x: 0, y: 0 }), undefined);
});

test('transport preserves request and completion payloads and exposes HTTP errors', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: !url.endsWith('/open'), json: async () => url.endsWith('/open') ? { error: 'unavailable' } : { closed: true } };
  };
  try {
    const client = new PreviewClient('/preview/test');
    assert.deepEqual(await client.request('state'), { closed: true });
    await client.request('save', { rooms: ['one'] });
    assert.deepEqual(JSON.parse(calls[1].options.body), { rooms: ['one'] });
    assert.equal(calls[1].options.method, 'POST');
    assert.equal(calls[1].options.headers['Content-Type'], 'application/json');
    await assert.rejects(client.request('open', { targetSid: 'missing' }), /unavailable/);
    await client.leave('close');
    assert.equal(calls.at(-1).options.keepalive, true);
  } finally { globalThis.fetch = originalFetch; }
});

class Element extends EventTarget {
  style = {};
  hidden = true;
  children = [];
  classes = new Set();
  captures = new Set();
  classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) };
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  setPointerCapture(id) { this.captures.add(id); }
  hasPointerCapture(id) { return this.captures.has(id); }
  releasePointerCapture(id) { this.captures.delete(id); }
  getBoundingClientRect() {
    return { left: Number.parseFloat(this.style.left ?? '100'), top: Number.parseFloat(this.style.top ?? '100'), width: 200, height: 100 };
  }
}

function panelScene(run) {
  const previous = { document: globalThis.document, width: globalThis.innerWidth, height: globalThis.innerHeight };
  globalThis.document = { createElement: () => new Element() };
  globalThis.innerWidth = 800;
  globalThis.innerHeight = 400;
  const node = new Element();
  const parts = new Map(['header', 'id', 'source', 'attrs', 'actions', 'close'].map(role => [`[data-role="${role}"]`, new Element()]));
  node.querySelector = selector => parts.get(selector);
  try { run(new ObjectInfoPanel(node), node, parts); }
  finally {
    globalThis.document = previous.document;
    globalThis.innerWidth = previous.width;
    globalThis.innerHeight = previous.height;
  }
}

test('property panel displays text, injects caller actions, replaces old content and stays in bounds', () => {
  panelScene((panel, node) => {
    panel.show({ id: '<script>', source: 'Entity', attrs: { value: '<b>text</b>' } }, { clientX: 790, clientY: 390 });
    assert.equal(panel.id.textContent, '<script>');
    assert.equal(panel.attrs.textContent, '{\n  "value": "<b>text</b>"\n}');
    assert.equal(node.hidden, false);
    assert.deepEqual(node.style, { left: '588px', top: '288px' });
    let clicked = false;
    const action = panel.addAction('Caller action', () => { clicked = true; });
    action.dispatchEvent(new Event('click'));
    assert.equal(clicked, true);
    assert.equal(panel.addAction('Unavailable', () => {}, true).disabled, true);
    globalThis.innerWidth = 500;
    globalThis.innerHeight = 250;
    panel.constrain();
    assert.deepEqual(node.style, { left: '288px', top: '138px' });
    panel.show({ id: 'Next', source: '' }, { clientX: 0, clientY: 0 });
    assert.equal(panel.actions.children.length, 0);
    assert.equal(panel.attrs.textContent, '{}');
    node.querySelector('[data-role="close"]').dispatchEvent(new Event('click'));
    assert.equal(node.hidden, true);
  });
});

test('property panel middle drag captures the pointer and releases it on cancel, hide and new content', () => {
  panelScene((panel, node) => {
    panel.show({ id: 'Object', source: '' }, { clientX: 20, clientY: 20 });
    mouse(panel.header, 'pointerdown', { button: 0, pointerId: 1 });
    assert.equal(panel.drag, undefined);
    mouse(panel.header, 'pointerdown', { button: 1, pointerId: 9, clientX: 50, clientY: 60 });
    assert.equal(panel.header.hasPointerCapture(9), true);
    mouse(panel.header, 'pointermove', { pointerId: 8, clientX: 90, clientY: 100 });
    assert.equal(node.style.left, '32px');
    mouse(panel.header, 'pointermove', { pointerId: 9, clientX: 90, clientY: 100 });
    assert.deepEqual(node.style, { left: '72px', top: '72px' });
    mouse(panel.header, 'pointercancel', { pointerId: 9 });
    assert.equal(panel.header.hasPointerCapture(9), false);
    assert.equal(node.classes.size, 0);
    mouse(panel.header, 'pointerdown', { button: 1, pointerId: 10 });
    panel.hide();
    assert.equal(panel.header.hasPointerCapture(10), false);
    mouse(panel.header, 'pointerdown', { button: 1, pointerId: 11 });
    panel.show({ id: 'Next', source: '' }, { clientX: 10, clientY: 10 });
    assert.equal(panel.header.hasPointerCapture(11), false);
  });
});
