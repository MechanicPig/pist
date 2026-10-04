// Browser-local camera and gestures; application actions are supplied by the page.
export class MapViewport {
  constructor(canvas) {
    this.canvas = canvas;
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
  }

  fit(bounds) {
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    this.scale = Math.min(width / Math.max(bounds.maxX - bounds.minX, 1), height / Math.max(bounds.maxY - bounds.minY, 1));
    this.offsetX = (width - (bounds.maxX - bounds.minX) * this.scale) / 2 - bounds.minX * this.scale;
    this.offsetY = (height - (bounds.maxY - bounds.minY) * this.scale) / 2 - bounds.minY * this.scale;
  }

  center(room, fit = false) {
    if (fit) {
      this.scale = Math.min(this.canvas.clientWidth / Math.max(room.width * 1.5, 1), this.canvas.clientHeight / Math.max(room.height * 1.5, 1));
    }
    this.offsetX = this.canvas.clientWidth / 2 - (room.x + room.width / 2) * this.scale;
    this.offsetY = this.canvas.clientHeight / 2 - (room.y + room.height / 2) * this.scale;
  }

  world(event) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left - this.offsetX) / this.scale, y: (event.clientY - rect.top - this.offsetY) / this.scale };
  }

  pan(dx, dy) {
    this.offsetX += dx;
    this.offsetY += dy;
  }

  zoom(event, factor) {
    const before = this.world(event);
    const rect = this.canvas.getBoundingClientRect();
    this.scale = Math.min(8, Math.max(.02, this.scale * factor));
    this.offsetX = event.clientX - rect.left - before.x * this.scale;
    this.offsetY = event.clientY - rect.top - before.y * this.scale;
  }

  beginFrame(ctx, pixelRatio = devicePixelRatio) {
    ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    ctx.clearRect(0, 0, this.canvas.clientWidth, this.canvas.clientHeight);
    ctx.setTransform(pixelRatio * this.scale, 0, 0, pixelRatio * this.scale, pixelRatio * this.offsetX, pixelRatio * this.offsetY);
  }
}

export class CanvasInteraction {
  constructor(viewport, {
    redraw, click, doubleClick, dragStart, dragMove, dragEnd, dragCancel, reset,
    zoomFactor = event => Math.exp(-event.deltaY * .001), threshold = 4,
  }) {
    this.viewport = viewport;
    this.callbacks = { redraw, click, doubleClick, dragStart, dragMove, dragEnd, dragCancel, reset, zoomFactor };
    this.threshold = threshold;
    this.drag = undefined;
    this.lastMiddleDown = undefined;
    this.listeners = new AbortController();
    const canvas = viewport.canvas;
    const options = { signal: this.listeners.signal };
    canvas.addEventListener('mousedown', event => this.start(event), options);
    canvas.addEventListener('mousemove', event => this.move(event), options);
    window.addEventListener('mouseup', event => this.end(event), options);
    window.addEventListener('blur', () => this.cancel(), options);
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      viewport.zoom(event, this.callbacks.zoomFactor(event));
      this.callbacks.redraw();
    }, { ...options, passive: false });
    canvas.addEventListener('dblclick', event => this.callbacks.doubleClick?.(viewport.world(event), event), options);
    canvas.addEventListener('contextmenu', event => event.preventDefault(), options);
    canvas.addEventListener('auxclick', event => { if (event.button === 1) event.preventDefault(); }, options);
  }

  start(event) {
    if (event.button > 2) return;
    if (event.button !== 0) event.preventDefault();
    if (event.button === 1 && this.callbacks.reset) {
      if (this.lastMiddleDown !== undefined && event.timeStamp - this.lastMiddleDown <= 350) {
        this.lastMiddleDown = undefined;
        this.cancel();
        this.callbacks.reset();
        return;
      }
      this.lastMiddleDown = event.timeStamp;
    }
    const start = this.viewport.world(event);
    this.drag = { button: event.button, x: event.clientX, y: event.clientY, start, moved: false };
    this.callbacks.dragStart?.(start, event);
    if (event.button !== 0) this.viewport.canvas.classList.add('panning');
  }

  move(event) {
    const drag = this.drag;
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < this.threshold) return;
    drag.moved = true;
    if (drag.button !== 0) this.viewport.pan(dx, dy);
    else this.callbacks.dragMove?.(drag.start, this.viewport.world(event), event);
    drag.x = event.clientX;
    drag.y = event.clientY;
    if (drag.button === 1) this.lastMiddleDown = undefined;
    this.callbacks.redraw();
  }

  end(event) {
    const drag = this.drag;
    if (!drag || event.button !== drag.button) return;
    this.drag = undefined;
    this.viewport.canvas.classList.remove('panning');
    if (drag.button === 0 && drag.moved) this.callbacks.dragEnd?.(event);
    else if (!drag.moved) this.callbacks.click?.(this.viewport.world(event), event);
  }

  cancel() {
    this.drag = undefined;
    this.lastMiddleDown = undefined;
    this.viewport.canvas.classList.remove('panning');
    this.callbacks.dragCancel?.();
  }

  dispose() {
    this.cancel();
    this.listeners.abort();
  }
}
