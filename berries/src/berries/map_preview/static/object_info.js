// Floating property panel mechanics; the page supplies object content and actions.
import { MouseButton } from './mouse.js';

export class ObjectInfoPanel {
  constructor(node) {
    this.node = node;
    this.header = node.querySelector('[data-role="header"]');
    this.id = node.querySelector('[data-role="id"]');
    this.source = node.querySelector('[data-role="source"]');
    this.attrs = node.querySelector('[data-role="attrs"]');
    this.actions = node.querySelector('[data-role="actions"]');
    this.drag = undefined;
    node.querySelector('[data-role="close"]').addEventListener('click', () => this.hide());
    this.header.addEventListener('pointerdown', event => {
      if (event.button !== MouseButton.MIDDLE) return;
      const rect = node.getBoundingClientRect();
      this.drag = { pointerId: event.pointerId, offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top };
      node.classList.add('dragging');
      this.header.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    this.header.addEventListener('pointermove', event => {
      if (this.drag?.pointerId !== event.pointerId) return;
      this.move(event.clientX - this.drag.offsetX, event.clientY - this.drag.offsetY);
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      this.header.addEventListener(name, event => this.finishDrag(event.pointerId));
    }
    this.header.addEventListener('auxclick', event => { if (event.button === MouseButton.MIDDLE) event.preventDefault(); });
  }

  move(left, top) {
    const margin = 12;
    const rect = this.node.getBoundingClientRect();
    const maximumLeft = Math.max(margin, innerWidth - rect.width - margin);
    const maximumTop = Math.max(margin, innerHeight - rect.height - margin);
    this.node.style.left = `${Math.min(Math.max(left, margin), maximumLeft)}px`;
    this.node.style.top = `${Math.min(Math.max(top, margin), maximumTop)}px`;
  }

  show({ id, source, attrs }, event) {
    this.finishDrag();
    this.id.textContent = id;
    this.source.textContent = source;
    this.attrs.textContent = JSON.stringify(attrs ?? {}, null, 2);
    this.actions.replaceChildren();
    this.node.hidden = false;
    this.move(event.clientX + 12, event.clientY + 12);
  }

  addAction(label, action, disabled = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.disabled = disabled;
    button.addEventListener('click', action);
    this.actions.append(button);
    this.constrain();
    return button;
  }

  finishDrag(pointerId = this.drag?.pointerId) {
    if (!this.drag || this.drag.pointerId !== pointerId) return;
    this.drag = undefined;
    this.node.classList.remove('dragging');
    if (this.header.hasPointerCapture(pointerId)) this.header.releasePointerCapture(pointerId);
  }

  hide() {
    this.finishDrag();
    this.node.hidden = true;
  }

  constrain() {
    if (this.node.hidden) return;
    const rect = this.node.getBoundingClientRect();
    this.move(rect.left, rect.top);
  }
}
