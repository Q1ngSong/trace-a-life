"use strict";
const filterButtons = document.querySelectorAll('[data-filter]');
filterButtons.forEach(button => button.addEventListener('click', () => {
  filterButtons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  let count = 0;
  document.querySelectorAll('.case-row').forEach(row => {
    row.hidden = button.dataset.filter !== 'all' && row.dataset.kind !== button.dataset.filter;
    if (!row.hidden) count++;
  });
  document.querySelector('#case-count').textContent = `${count} 份研究档案`;
}));
document.querySelector('#copy-prompt').addEventListener('click', async () => {
  const status = document.querySelector('#copy-status');
  try {
    await navigator.clipboard.writeText(document.querySelector('#research-prompt').textContent);
    status.textContent = '已复制，可以粘贴给你的 Agent。';
  } catch {
    status.textContent = '当前浏览器无法自动复制，请选中上方文字复制。';
  }
});
if ('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.remove('pending');
      observer.unobserve(entry.target);
    }
  }), {threshold: 0.08});
  document.querySelectorAll('.section-heading, .boundary, .method-list li').forEach(node => {
    node.classList.add('reveal-in', 'pending');
    observer.observe(node);
  });
}
